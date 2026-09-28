# ADR-023: Embed chunks with a small local model, run by ONNX Runtime

Status: accepted (v8)

## Context

Keyword search misses questions that use different words from the article. In the v6 evaluation,
paraphrased questions scored **hit@3 0.47**, and 20% of them returned nothing at all ("Can I get
my money back?" shares no word with the refund policy). Finding text by meaning needs
**embeddings**: a model turns each chunk and each question into a vector of numbers, and texts
with similar meanings get nearby vectors.

This decision covers two choices: which model, and which library runs it. It has to run on a
laptop CPU, for free, with no paid external API.

## Options

Models, both small enough for a CPU and both 384 numbers per text:

1. **`sentence-transformers/all-MiniLM-L6-v2`**: 22 M parameters, the most widely used small
   sentence-embedding model.
2. **`BAAI/bge-small-en-v1.5`**: 33 M parameters, stronger on retrieval benchmarks. It needs an
   instruction added to the front of every question ("Represent this sentence for searching
   relevant passages: ").

Libraries:

1. **sentence-transformers**: the standard library for these models, built on PyTorch.
2. **fastembed**: runs the same models exported to ONNX, with ONNX Runtime (a small inference
   engine) instead of PyTorch.
3. A paid embedding API: ruled out, because the project uses no paid services.

## Decision

`all-MiniLM-L6-v2`, run by **fastembed**. The model name is a setting (`EMBEDDING_MODEL`), and
every document records which model embedded it (`documents.embedding_model`).

## Why?

**Model.** A quick in-process check on the evaluation set (30 questions, 20 articles), run before
building anything:

| | hit@3 | MRR | paraphrase hit@1 | extra step |
|---|---|---|---|---|
| all-MiniLM-L6-v2 | 0.93 | 0.87 | 0.60 | none |
| bge-small-en-v1.5 | 0.93 | 0.90 | 0.73 | instruction on every question |

Both find the right article in the top 3 equally often. bge ranks it first slightly more often, but
it is a larger model and needs an extra rule that is easy to forget. 30 questions cannot separate
them reliably, so the simpler one wins. The evaluation will show when this stops being enough.

**Library.** sentence-transformers was built first and then replaced, because of measurement:

| Measured on the development laptop | sentence-transformers | fastembed |
|---|---|---|
| Vectors for the 20 evaluation chunks | same | same: cosine similarity 1.00000 for every chunk |
| Rank of the right article, all 30 questions | same | same |
| Import time | 90–150 s (3 runs) | 21–25 s (2 runs) |
| Model load, already downloaded | 13.6 s | 7.0 s |
| Largest installed pieces | PyTorch, plus transformers 32 MB and sympy 73 MB | onnxruntime 46 MB |

The import time is mostly the laptop's slow disk. Profiling showed 25,736 file-system `stat` calls
at about 1.5 ms each, because transformers scans its own model folders when imported. A faster
machine would shrink both columns. The difference would stay, though, because fastembed does not
have that work to do.

Import time matters here because it is paid by **both** processes: the worker when it starts, and
the API on its first semantic search. On Linux, the default PyTorch install also includes GPU
libraries measured in gigabytes, which the Docker image would carry and never use.

## Trade-offs

Gain:

- Paraphrase hit@3 went from 0.47 to 0.87, measured through the API (see v8).
- Everything runs locally. The model is downloaded once (~90 MB, into `models/embeddings`), and
  after that no network is needed.
- The install is small, and it has no PyTorch.

Lose:

- **CPU time, a lot of it.** On this laptop (on battery, other programs running), embedding ran at
  1–10 chunks per second. A 300-page PDF (1,246 chunks) took about **7.5 minutes** in the worker,
  against ~9 s for extraction and chunking in v7. v7 moved this work off the request, which is why
  users do not feel it directly. It still decides how fast documents become searchable.
- **A second copy of the model in memory**, one in the worker and one in the API. The API needs it
  to embed the question.
- **fastembed only runs models that have been exported to ONNX.** A model that has not been
  exported would mean going back to sentence-transformers.
- **Changing models means re-embedding everything.** Vectors from different models cannot be
  compared, so semantic search ignores documents embedded by another model until
  `scripts/embed_existing_documents.py` re-embeds them.

## Future Trigger

- **The evaluation plateaus below what users need**: try a stronger model such as bge-small. The
  switch is a setting plus a re-embedding run, and the evaluation set decides.
- **Embedding throughput limits ingestion** (a backlog of `queued` documents): more worker
  processes, a machine with more cores, or a GPU worker. The model-service split (v14) is where
  this belongs.
- **Question-embedding latency dominates search**: a dedicated model service, or caching the
  vectors of frequent questions.
