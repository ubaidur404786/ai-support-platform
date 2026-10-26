# Decision

Run the embedding model in its own small HTTP service (`app/embedding_service/`), called by both the API and the worker.

## Context

Two processes need embeddings. The API embeds each search question, and the worker embeds each chunk of an uploaded document. Until v13, each process loaded its own copy of the model and of ONNX Runtime.

Measured on the development laptop before the change:

- the API process used 975 MB and the worker 556 MB
- the first semantic search after the API started took **20.9 s**, because the API loaded the model during that request

The v12 model service had already shown that the pattern works here: one process owns the model, and the others call it.

## Options

1. **Leave it as it is.** It works, but every API copy we add in Kubernetes (v16) brings its own model, and the first search on each copy is slow.
2. **Load the model when the API starts.** This fixes the slow first search, but every process still holds a copy.
3. **One embedding service shared by the API and the worker.** This is the option we chose.

## Decision

Option 3. The model is loaded once, when `app/embedding_service/main.py` starts. `app/documents/embeddings.py` keeps the same `embed_texts()` function, but now it sends the texts over HTTP, at most 64 per request.

## Why?

- The API and the worker no longer need ONNX Runtime or fastembed at all. Their container images in v14 get smaller, and they start faster.
- The service can be scaled on its own later. Embedding is CPU work, while the API mostly waits on the network and the database.
- It is the same pattern as v12, so there is nothing new to learn or to maintain.

## Trade-offs

- **Gained:** one copy of the model, and no 20 s first search (377 ms after the change). The API went from 975 to 852 MB and the worker from 556 to 421 MB.
- **Total memory went up with one API process:** 1,531 MB before, 1,803 MB after, because the new service runs its own Python, FastAPI and NumPy. It pays back from the third API copy.
- **Lost:** there is one more process that can be down. While it is down, semantic search and answers return 503 and new uploads wait in the queue. Keyword search and tickets keep working. Each embedding also costs a network round trip of a few milliseconds.
- The service checks the model name that callers send and refuses a mismatch. Vectors from different models cannot be compared, so a silent mismatch would make search quietly wrong.

## Future Trigger

- If one service cannot keep up with uploads, run several copies behind a Kubernetes Service. It keeps no state, so that is safe.
- If embedding needs a GPU, only this service's image changes.
