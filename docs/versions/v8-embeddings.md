# v8 — Embeddings: search by meaning

API 0.8.0 · classifier model v0.1.0 unchanged · embedding model `sentence-transformers/all-MiniLM-L6-v2` · 207 tests passing (1,378 s on a heavily loaded laptop)

![v8 architecture](../architecture/v8.svg)

## Recap: where v7 left us

Priya, a support agent at Acme, has a customer asking *"Can I get my money back?"*. The refund
policy is in the knowledge base, and she searches for the customer's words. She gets **nothing**.
The article says "refunds are issued within ten business days", and keyword search only finds
chunks that share words with the question. "money", "back" and "get" never appear in it.

What broke, in one sentence: keyword search found the right article in the top 3 for only **47%**
of paraphrased questions, and returned nothing at all for 20% of them (v6 evaluation, 30
questions).

What v7 changed: uploads became `202 queued`, and a worker process (`python -m app.worker`)
extracts and chunks each document outside the request. `/health` stayed at ~20 ms P50 while
large PDFs were processed, against 335–729 ms before.

What was still wrong, and became v8: search itself. Because v7 moved ingestion off the request
path, extra work per chunk at ingest time no longer costs users anything directly. That made
embeddings affordable.

## Problem

Users describe their problem in their own words, and help articles use the product's words.
Keyword search (PostgreSQL full-text) can only match words:

| Question type (15 each) | hit@3 | returned nothing |
|---|---|---|
| lexical ("How long does a refund take?") | 1.00 | 0% |
| paraphrase ("Can I get my money back?") | **0.47** | **20%** |

Every paraphrase miss was a vocabulary mismatch: "money back" vs "refund", "admin" vs
"Administrators", "talk to a human" vs "contact support".

## Current Architecture

```
GET /documents/search?q=... → service: keep letters/digits → repository:
    WHERE organization_id = … AND search_vector @@ to_tsquery('w1 | w2 …') ORDER BY ts_rank LIMIT n
```

## Why the Old Design Is Not Enough

No keyword trick solves this. Stemming already maps "refunds" to "refund". A synonym list would
have to predict every way a customer might phrase every idea, and still miss "forget everything
you know about me" → *privacy requests*. What is needed is a comparison of **meaning**, which a
word index cannot do.

## Solution

**Embeddings**: a small neural network turns a piece of text into 384 numbers (a vector). Texts
with similar meanings get vectors that point in similar directions. The score between a question
and a chunk is the **cosine similarity** of their vectors: 1.0 means the same meaning, around 0
means unrelated.

```
INGEST (worker process)
  claim document → extract → chunk (unchanged)
    ▼ embeddings.embed_texts        256 chunks at a time → 384 float32 numbers each
    ▼ _heartbeat                    after every batch: "still working" (see Trade-offs)
    ▼ ONE transaction               chunks + their embeddings + documents.embedding_model + "ready"

SEARCH (API process)
  GET /documents/search?q=Can I get my money back?&mode=semantic
    ▼ service.search       same empty-query rule (422); embed the question   ~141 ms
    ▼ repository.semantic_search
         SELECT id, embedding FROM document_chunks JOIN documents
          WHERE organization_id = …  AND documents.embedding_model = <current model>
         matrix (n × 384) @ question (384)  → n scores → best `limit`
         second SELECT: text + title for the winners only
  200 {"mode": "semantic", "results": [{"document_title": "Refund policy", "rank": 0.52, ...}]}

mode=keyword (the default) is unchanged.
```

Files:

| File | What changed |
|---|---|
| `app/documents/embeddings.py` | **new**: loads the model once per process (`lru_cache`), `embed_texts` → normalised float32 array, `to_bytes` / `from_bytes`, `EmbeddingUnavailable` |
| `app/documents/models.py` | `DocumentChunk.embedding` (bytea, 1,536 bytes); `Document.embedding_model` (which model made the vectors) |
| `alembic/versions/d7a2c4e9b1f5_...py` | adds both columns, nullable; existing chunks stay without embeddings until backfilled |
| `app/documents/processing.py` | embeds every chunk in batches with a heartbeat; `embed_existing_document` for old documents |
| `app/documents/repository.py` | `semantic_search`: brute force over the organisation's vectors |
| `app/documents/service.py`, `router.py`, `schemas.py` | `mode=keyword|semantic`; 503 "try mode=keyword" if the model cannot run; `mode` in the response |
| `app/worker.py` | loads the model at start, before the first document |
| `app/core/config.py` | `EMBEDDING_MODEL`, `EMBEDDING_CACHE_DIR` (`models/embeddings`, gitignored) |
| `scripts/embed_existing_documents.py` | **new**: backfills documents processed before v8 (or by another model), optionally one organisation |
| `scripts/evaluate_retrieval.py` | now runs keyword-any, keyword-all **and** semantic on the same 30 questions |
| `scripts/measure_semantic_search.py` | **new**: model load, embedding speed, and search latency at 1k / 10k / 50k chunks |

## Why This Solution?

**Why this model, and why fastembed?** ([ADR-023](../adr/ADR-023-local-embedding-model-with-onnx-runtime.md))
`all-MiniLM-L6-v2` matched `bge-small-en-v1.5` on hit@3 (0.93 each) in an in-process pre-check. It
is smaller and needs no special prefix on questions. sentence-transformers (PyTorch) was built
first. It was replaced by **fastembed** (ONNX Runtime), which produces **identical vectors**
(cosine 1.00000 on every chunk, and the same rank for all 30 questions) but imported in 21–25 s
instead of 90–150 s on this laptop, with no PyTorch in the install.

**Why embed in the worker, at ingest time?** A chunk's vector never changes, so it is computed once
and stored. Embedding at search time would redo that work on every question. v7 is what makes
this affordable: ingest-time work no longer blocks any request.

**Why record `embedding_model` per document?** Vectors from two different models live in different
spaces. Comparing them gives a number, but it is meaningless. Semantic search only compares
documents embedded by the model it is using, and the backfill script re-embeds the rest. This is
the first piece of **model versioning** for retrieval, the same idea as `model_version` on tickets.

**Why brute force, not pgvector yet?** ([ADR-024](../adr/ADR-024-brute-force-vector-search-in-numpy.md))
v8 answers one question: "do embeddings find better answers?" Brute force is exact, so it is also
the baseline any future index has to match. Its cost was measured on purpose, and it is v9's
trigger.

**Why keep `keyword` as the default?** Existing clients see no change, and keyword search is still
better in two ways: it is 5–70× faster, and it returns *nothing* when nothing matches (see
Trade-offs). Combining the two is v11-hybrid-retrieval.

### Failure-first: what happens when...

| Situation | What happens | Proved by |
|---|---|---|
| The model cannot load or run during a **search** | 503 "Semantic search is unavailable; try mode=keyword". Keyword search keeps working | `test_an_unavailable_model_is_503_and_keyword_search_still_works` |
| The model cannot run while **processing** | Treated as an unexpected error: back to `queued`, retried up to 3 times. Not `failed` at once, because the file is fine | `test_an_unavailable_model_retries_instead_of_failing_the_file` |
| The model cannot load when the **worker starts** | Logged at once; the worker keeps running and documents are retried | `test_the_worker_starts_even_if_the_model_cannot_load` |
| Embedding a big document takes longer than the 5-minute stale timeout | The heartbeat after every 256 chunks keeps it alive. Without it, a second worker would take it over again and again until it `failed` | `test_a_long_embedding_keeps_the_document_alive`, **confirmed to fail with the heartbeat disabled** |
| Documents were embedded by a different model | Left out of semantic search, still found by keyword search | `test_documents_embedded_by_another_model_are_not_compared` |
| Documents from before v8 have no embeddings | Same as above until `scripts/embed_existing_documents.py` runs | `test_documents_from_before_v8_can_be_embedded_later` |
| Another organisation's chunks | Filtered on `document_chunks.organization_id`, as in keyword search | `test_semantic_search_stays_inside_the_organisation` |
| A question the knowledge base cannot answer | **Results are still returned**, with a low score (< 0.3 for "What is the capital of France?") | `test_semantic_search_always_returns_the_closest_chunks` |

## New Trade-offs

- **Semantic search never says "no match".** Keyword search returns nothing for a question with no
  shared words. Semantic search always returns the closest chunks, however far away they are. For
  search that is acceptable. For v10 (RAG), where retrieved chunks become the "facts" of a
  generated answer, it is a hallucination risk. It needs a score threshold, chosen by measurement,
  not by guessing.
- **Ingestion became much slower.** Embedding ran at **1–10 chunks per second** on this laptop. A
  300-page PDF (1,246 chunks) was ready about **7.5 minutes** after upload, against 8.5–10.3 s in v7. Users
  do not wait on the request, thanks to v7, but "searchable" is now minutes away for large files.
  With one worker, documents queue behind each other.
- **Semantic search latency grows with the organisation.** It took 825 ms at 1,000 chunks, 4.4 s at
  10,000 and **10.5 s at 50,000** (P50). Almost all of that is reading every vector out of
  PostgreSQL (13.0 s of a 13.7 s in-process query), not the maths (36 ms). This is ADR-024's
  trigger.
- **The API process now runs a neural network.** Each semantic query spends ~141 ms of CPU
  embedding the question. The first query after the API starts also loads the model (37.7 s
  measured, most of it importing). Keyword search does not load the model.
- **Two copies of the model** (~90 MB on disk, plus memory), one in the API and one in the worker.
- **Changing the model means re-embedding everything.** The backfill script does it. On this
  laptop, the dev database's 108,260 chunks would take hours, so the script can be limited to one
  organisation.
- **The stale timeout needed a heartbeat.** v7's rule was "processing for more than 5 minutes means
  the worker died". v8 made normal documents take longer than that, so `processing_started_at` is
  now refreshed after every batch. It means "last sign of life".

## What Changed

- `mode=semantic` on `GET /documents/search`; `mode` in the response.
- Every new chunk is stored with its embedding. Every new document records its `embedding_model`.
- The worker loads the model at start and sends heartbeats while embedding.
- A backfill script for older documents; evaluation and measurement scripts extended.
- Dependencies: `fastembed` (ONNX Runtime, tokenizers, huggingface-hub). No PyTorch.
- **Replaced during the version:** sentence-transformers. It was implemented, tested, and then
  removed after measuring its import time (ADR-023).
- **Found by measuring, not by the tests:** the 5-minute stale timeout was shorter than a real
  document's processing time. The tests used small documents, so they could not see it. Found by
  watching one 300-page PDF take ~7.5 minutes. Fixed with the heartbeat, and a test now covers it.

## How to Test

```bash
pip install -r requirements.txt
alembic upgrade head
pytest -v
```

Run it, with two terminals:

```bash
uvicorn app.main:app          # terminal 1
python -m app.worker          # terminal 2: downloads the model on first start (~90 MB)
```

```bash
curl -X POST http://127.0.0.1:8000/documents -H "Authorization: Bearer $TOKEN" -F "file=@evaluation/knowledge_base/refund-policy.md"
curl -G http://127.0.0.1:8000/documents/search -H "Authorization: Bearer $TOKEN" --data-urlencode "q=Can I get my money back?"
curl -G http://127.0.0.1:8000/documents/search -H "Authorization: Bearer $TOKEN" --data-urlencode "q=Can I get my money back?" -d mode=semantic
```

Documents uploaded before v8 have no embeddings yet. The script prints which organisations are
waiting:

```bash
python scripts/embed_existing_documents.py --organization-id 6
```

Evaluate and measure (with `DB_ECHO=false`):

```bash
python scripts/evaluate_retrieval.py --output evaluation/results/v8-semantic-search.json
python scripts/measure_semantic_search.py
```

## Expected Result

- The worker logs `Loading embedding model ...`, then `Worker started`, then
  `Document 1 ready: 1 chunks`.
- The keyword search for "Can I get my money back?" returns `"results": []`. The semantic one
  returns the refund policy first, with a higher `rank` (cosine similarity) than the password article.
- `GET /documents/search?q=???&mode=semantic` → 422, as for keyword.

## Measurements

Local Windows 11 laptop, PostgreSQL 17 in Docker, one uvicorn process, one worker,
`DB_ECHO=false`, no `--reload`, Python `httpx` client. **The machine was noisy.** It was on battery
for part of the session and under heavy background load for all of it (one system service used
about half a core; the CPU ran at 1.54 of 2.1 GHz). Simple operations varied by 2–10× between
runs: the same login took 0.75 s in one run and 6.45 s in another. Treat absolute times as "this
laptop, today". The retrieval quality numbers do not depend on the machine.

**1. Retrieval quality**: same 20 articles, same 30 questions as v6, through the API
(`evaluation/results/v8-semantic-search.json`). The keyword rows reproduce v6's numbers exactly:

| | hit@1 | hit@3 | hit@5 | MRR | empty |
|---|---|---|---|---|---|
| keyword (`any`), overall | 0.60 | 0.73 | 0.73 | 0.66 | 10% |
| **semantic**, overall | **0.80** | **0.93** | **1.00** | **0.87** | **0%** |
| keyword, paraphrase | 0.20 | 0.47 | 0.47 | 0.31 | 20% |
| **semantic**, paraphrase | **0.60** | **0.87** | **1.00** | **0.74** | **0%** |
| keyword and semantic, lexical | 1.00 | 1.00 | 1.00 | 1.00 | 0% |

Two paraphrases are still outside the top 3: "The six digit number from my phone app is not
accepted" (→ two-factor authentication, ranked 4th) and "Can I close our company account for
good?" (→ delete account, ranked 4th). With n=30, one question is 3.3 points, and the articles and
questions share one author, so these numbers show the direction, not real-world accuracy.

**2. Search latency**: `scripts/measure_semantic_search.py`, synthetic chunks with random
normalised vectors, `limit=5`, 30 requests per mode:

| Chunks | keyword P50 / P95 | semantic P50 / P95 |
|---|---|---|
| 20 (evaluation set) | 64 ms | 627 ms |
| 1,000 | 65 / 339 ms | 825 / 1,999 ms |
| 10,000 | 94 / 282 ms | 4,428 / 6,337 ms |
| 50,000 | 152 / 245 ms | **10,498 / 25,589 ms** |

In-process at 50,000 chunks: reading the vectors took 13,008 ms, joining them into an array 630 ms,
and scoring plus sorting 36 ms.

**3. The model's cost:**

| | Measured |
|---|---|
| Model load, already downloaded (in-process) | 7.0–15.0 s |
| First semantic search after the API starts (includes the import) | 37.7 s |
| Embed one question (in-process, P50 of 30) | 141 ms |
| Embed chunks of ~800 characters | 1–10 chunks/s (500 in 203 s; 64 in 7–58 s depending on threads and batch size) |
| 300-page PDF (1,246 chunks), upload → ready, one worker | ~7.5 min (v7: 8.5–10.3 s) |
| Backfill of the 20 evaluation documents (including model load) | 54.1 s |

**Not measured in v8:** `/health` latency while the worker embeds. ONNX Runtime uses several CPU
cores, so the API could slow down through core contention, although not through the GIL. The v7
comparison script was started alternating v7 and v8. It was stopped after the first v8 document
took ~7.5 minutes, which would have made one round take hours. This is recorded rather than
guessed.

## Interview Explanation

"Keyword search found the right article for only 47% of paraphrased questions, because it matches
words, not meaning. I added embeddings: a small local model, all-MiniLM-L6-v2, turns each chunk
into 384 numbers in the background worker, and the question at search time. Search compares them
by cosine similarity. On the same 30-question evaluation, paraphrase hit@3 went from 0.47 to 0.87
and overall from 0.73 to 0.93, and no question came back empty. I chose the model and the runtime
by measurement: two models tied on the evaluation, so I took the simpler one. The ONNX runtime gave
identical vectors to the PyTorch one, with a much faster start and a much smaller install. I stored
which model produced each document's vectors, because vectors from different models can't be
compared. I also deliberately used exact brute-force search, and measured its cost: at 50,000
chunks a query takes about 10 seconds, almost all of it moving vectors out of the database, while
the maths takes 36 ms. That's the trigger for a vector index in the next version. Two other
findings: embedding made a 300-page PDF take minutes instead of seconds, which broke my 5-minute
crashed-worker timeout, so I added a heartbeat. And semantic search never returns 'no results',
which matters once retrieved text feeds a generated answer."

## Next Possible Limitation

- **Semantic search cost grows with the organisation** (10.5 s P50 at 50,000 chunks). **Trigger for
  v9-vector-search**: pgvector, with the distance computed in the database and an HNSW index,
  measured against this exact baseline for both latency *and* recall.
- **Embedding throughput** (1–10 chunks/s here) sets how fast documents become searchable. More
  workers, more cores, or a separate model service (v14).
- **No "I don't know".** A relevance threshold is needed before v10 turns retrieved chunks into
  generated answers.
- **The two modes are separate.** Keyword is still perfect on lexical questions and fast. Hybrid
  retrieval (v11) combines them.
