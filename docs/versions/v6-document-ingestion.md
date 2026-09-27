# v6 — Document ingestion and keyword search

API 0.6.0 · model v0.1.0 unchanged · 180 tests passing

![v6 architecture](../architecture/v6.svg)

## Recap: where v5 left us

Maya at Acme logs in once and classifies about 20 tickets an hour. Before v5, a script could
send wrong passwords to `/auth/login` in a loop, and each guess cost ~890 ms of bcrypt. v5 gave
every caller a token bucket that is checked **before** the expensive work runs: 30 wrong logins
went from 29.06 s of server time to 10.54 s, and a refused attempt costs 6.2 ms.

The platform could now sort Maya's tickets safely. It still could not *answer* one. When a
customer writes "how long does a refund take?", the answer is in Acme's refund policy, and the
refund policy existed only as a file on someone's laptop.

## Problem

The platform is called a *knowledge* automation platform, and it held no knowledge.

- There was nowhere to put a document. A ticket is one short text with one label; a 30-page
  policy is long, structured, and useful in pieces.
- Nothing could read a file. Every endpoint took JSON.
- Nothing could find a passage. `WHERE text = ...` cannot answer "which paragraph is about
  refunds?"

Every later AI version depends on this one. Embeddings (v8), vector search (v9) and generated
answers (v10) all need documents that are already stored, split up and owned.

## Current Architecture

Before this version:

```
Client → auth + rate limit → router.py → service.py → repository.py → PostgreSQL (tickets, users, organizations)
                                             └→ classifier.predict()
```

## Why the Old Design Is Not Enough

This version's trigger is, like v4's, a **precondition** rather than a measured failure. The
retrieval half of the roadmap cannot start without stored, chunked, organisation-owned text. What
v6 *does* do is turn that precondition into numbers: how long ingestion takes, what search costs,
and how often keyword search finds the right article. Those numbers are the triggers for v7 and v8.

## Solution

```
refund-policy.pdf + Bearer token
   │
   ▼  core/request_limits.py     Content-Length > 5 MB + 64 KB → 413, before anything else runs
   ▼  get_current_user → limit_ingestion (20/min per user)
   ▼  documents/router.py        read at most 5 MB + 1 byte
   ▼  documents/service.py       SHA-256 → already stored? 409 (before any extraction)
   ▼  documents/extraction.py    detect type from name AND first bytes → text (pypdf for PDF)
   ▼  documents/chunking.py      ~800-character chunks, 100 characters of overlap
   ▼  documents/repository.py    ONE transaction: 1 row in documents + N rows in document_chunks
   ▼                             PostgreSQL computes each chunk's tsvector and indexes it (GIN)
201 {"id": 12, "title": "Refund policy", "chunk_count": 14, "page_count": 3, ...}

GET /documents/search?q=how long does a refund take
   ▼  service.py        keep letters/digits only → ["how","long","does","a","refund","take"]
   ▼  repository.py     WHERE organization_id = 3 AND search_vector @@ to_tsquery('how | long | … | take')
   ▼                    ORDER BY ts_rank(...) DESC LIMIT 5
{"results": [{"document_id": 12, "document_title": "Refund policy", "chunk_index": 0, "text": "Refunds are issued…", "rank": 0.46}]}
```

Endpoints: `POST /documents`, `GET /documents` (paged, newest first), `GET /documents/{id}`,
`DELETE /documents/{id}` (chunks removed by `ON DELETE CASCADE`), `GET /documents/search`
(`limit` ≤ 20, `match=any|all`). All of them need a token, and all of them are scoped to the
caller's organisation. Another organisation's document is 404 everywhere, search included.

## Why This Solution?

### Chunks, not whole documents

A search should return the paragraph that answers the question, not a PDF. Embeddings in v8 will
be computed per chunk, and generated answers in v10 will quote chunks. Deciding the unit now means
v8 adds a column rather than redesigning storage. Overlap exists because a boundary can fall in
the middle of the sentence that matters. With 100 repeated characters, that sentence appears whole
in at least one of the two chunks. The 800-character size is a starting guess, recorded as one.

### PostgreSQL full-text search, not Elasticsearch and not embeddings yet

| Option | What it needs | Why not now |
|---|---|---|
| `LIKE '%refund%'` | nothing | no ranking, no stemming ("refunds" ≠ "refund"), cannot use an ordinary index |
| **PostgreSQL `tsvector` + GIN** | **one column and one index** | — chosen: stemming, stop words and ranking, inside the database we already run |
| Elasticsearch / OpenSearch | a new service, a second copy of the data, sync between them | no measured need; one more thing to fail |
| Embeddings + vector search | a model, a vector index, an evaluation | that is v8–v9. Without a keyword baseline, there is no way to show they help |

The last row is the main reason this version includes search at all. **Keyword search is the
baseline that embeddings have to beat.** Measured on 30 questions, it finds the right article in
the top 3 for **100%** of questions that reuse the article's words and **47%** of questions that
don't. That second number is v8's trigger.

### `ts_rank`, chosen by measurement over `ts_rank_cd`

The first version used `ts_rank_cd`, which also rewards query words that appear close together.
Over 39,580 matching chunks it took **6,368 ms**; `ts_rank` took **64 ms** for the same query.
Ranking is computed for every *matching* chunk before `LIMIT` applies, so a costly per-row function
multiplies. The evaluation did not get worse: hit@3 was 0.73 with both, and MRR went from 0.64 to
0.66. See ADR-018.

### "any" matching by default, and why "all" is exposed too

`to_tsquery` combines words with `&` (all must appear) or `|` (any may appear). Questions rarely
share *every* word with the passage that answers them. With `all`, even questions that reuse the
article's words failed 20% of the time ("How long does a refund take?" — the article never says
"long"), and every paraphrase returned nothing. `any` plus ranking is the fairer baseline. `all` is
kept as a query option so the evaluation can show the difference.

### The query is rebuilt from words, never passed through

`to_tsquery` has its own syntax (`& | ! ( ) :`). User input goes through
`re.findall("[a-z0-9]+")` in the service, and the repository re-checks every word against
`^[a-z0-9]+$` before building the query. `refund') | !(x` is harmless (tested), and a query with
no words at all is 422 rather than a database error.

### Duplicates by content hash, per organisation

The SHA-256 of the bytes is checked **before** extraction, so re-uploading a 300-page PDF costs a
hash, not six seconds. A unique constraint on `(organization_id, sha256)` closes the race between
two simultaneous uploads (the same check-then-act problem v4 solved for emails). It is per
organisation because two companies may upload the same public PDF and each must own its copy.

### A 413 before authentication

This was not planned. It was measured. FastAPI parses a multipart body **before** it runs any
dependency, authentication included. An anonymous 200 MB upload was received in full (4,146 ms)
and only then answered with 401. v5's rule was "refuse before the expensive work", and here
receiving the upload *was* the expensive work. A small middleware now answers from the
`Content-Length` header alone: **413 in 1.4–3.2 ms**, with no body bytes read. See ADR-020.

### Store the extracted text, not the original file

`documents.content` holds what we extracted. The original bytes are discarded. That is enough to
re-chunk later with a different size, but not enough to re-extract with a better PDF reader or to
let a user download the original. Object storage (MinIO) solves that, and adding it now would be
a second storage system with no measured need. See ADR-019.

## New Trade-offs

**Gained:** the platform can hold knowledge; search is organisation-scoped and bounded; duplicate
uploads cost nothing; a baseline retrieval score exists; oversized uploads are refused before any
work.

**Lost, or newly owed:**

- **Uploads run inside the HTTP request.** A 1 MB text file holds a worker for 2.4 s, a 4.9 MB one
  for 8.4 s, and a 300-page PDF for 5.8 s (P50). During that time the worker serves nothing else.
- **Keyword search does not understand meaning.** Hit@3 is 0.47 on paraphrased questions, and 20%
  of them return nothing at all.
- **Search cost grows with the number of matching chunks**, not with `limit`. At 39,588 chunks a
  common-word query takes 108 ms, most of it spent ranking every match. This is v3's problem again,
  inside the database. It is bounded for now, but it is not flat.
- **Originals are not kept** (see above).
- **Scanned PDFs are refused** (422). There is no OCR.
- **Chunked (streamed) uploads without `Content-Length` bypass the middleware.** The endpoint still
  bounds what it processes, but not what it receives. That belongs to a reverse proxy.
- **A chunk stores its organisation twice** (on the chunk and via its document). It is denormalised
  on purpose so the tenant filter never depends on a JOIN, but the two copies must agree. They are
  written together in one transaction, and nothing else writes them.
- **The test suite is 187 s** (180 tests).

## What Changed

| File | Change |
|---|---|
| `app/documents/models.py` | **new** — `Document`, `DocumentChunk` (generated `tsvector` column, GIN index, `ON DELETE CASCADE`, unique `(organization_id, sha256)`) |
| `alembic/versions/8c3e5d7a1f20_add_documents_and_chunks.py` | **new** migration. `alembic check` reports no drift; downgrade/upgrade verified |
| `app/documents/extraction.py` | **new** — type detection by extension + magic bytes, UTF-8 / pypdf extraction, NUL removal, page limit, empty-text refusal, title derivation |
| `app/documents/chunking.py` | **new** — paragraph → sentence → word breaks, overlap, exact offsets |
| `app/documents/repository.py` | **new** — one-transaction add, unique-violation (SQLSTATE 23505) vs other integrity errors, scoped get/list/count/delete, ranked search |
| `app/documents/service.py` | **new** — hash-before-extract duplicate check, ingest, query cleaning, clamping |
| `app/documents/router.py`, `schemas.py`, `dependencies.py` | **new** — five endpoints; `/search` declared before `/{document_id}` |
| `app/core/request_limits.py` | **new** — body-size middleware |
| `app/auth/dependencies.py` | `limit_ingestion`, a per-user budget separate from inference |
| `app/core/config.py`, `app/main.py`, `.env.example` | version 0.6.0; document, chunk, search and ingestion-limit settings; middleware and router wiring |
| `alembic/env.py` | registers the document models |
| `requirements.in` / `requirements.txt` | `python-multipart`, `pypdf` |
| `tests/test_ingestion_pipeline.py` | **new** — 29 tests, no HTTP and no database |
| `tests/test_documents_api.py` | **new** — 31 API tests |
| `tests/pdf_factory.py` | **new** — writes real PDFs in memory, with no extra dependency |
| `tests/conftest.py` | truncates the two new tables |
| `evaluation/knowledge_base/*.md`, `evaluation/retrieval_questions.jsonl` | **new** — 20 help articles, 30 questions (15 lexical, 15 paraphrase) |
| `evaluation/results/v6-keyword-search*.json` | the measured baseline, for both ranking functions |
| `scripts/evaluate_retrieval.py`, `scripts/measure_ingestion.py` | **new** |

## How to Test

```bash
docker compose up -d
```

```bash
pip install -r requirements.txt
```

```bash
alembic upgrade head
```

```bash
pytest -v
```

Try it (with a `$TOKEN` from `/auth/login`):

```bash
curl -X POST http://127.0.0.1:8000/documents -H "Authorization: Bearer $TOKEN" -F "file=@evaluation/knowledge_base/refund-policy.md"
```

```bash
curl -G http://127.0.0.1:8000/documents/search -H "Authorization: Bearer $TOKEN" --data-urlencode "q=how long does a refund take"
```

Run the retrieval evaluation against a running server:

```bash
python scripts/evaluate_retrieval.py --output evaluation/results/v6-keyword-search.json
```

## Expected Result

- `pytest`: **180 passed**.
- The upload returns 201 with `"title": "Refund policy"` and `"chunk_count": 1`.
- The search returns that chunk first.
- The evaluation prints hit@3 **0.73** overall with `match=any`: 1.00 for lexical questions and
  0.47 for paraphrases.

## Measurements

All measured locally in one session: `DB_ECHO=false`, no `--reload`, Python `httpx` client,
PostgreSQL 17 in Docker.

**Ingestion time, end to end** (`scripts/measure_ingestion.py`, ingestion limit raised for the run).
"Extract" and "chunk" were timed in the measuring process, not inside the server.

| File | Chunks | Extract | Chunk | `POST /documents` P50 | n (min–max) |
|---|---|---|---|---|---|
| txt 10 KB | 17 | 1 ms | 0 ms | **165 ms** | 5 (152–353) |
| txt 1 MB | 1,737 | 178 ms | 36 ms | **2,372 ms** | 5 (2,089–2,874) |
| txt 4.9 MB | 8,518 | 1,171 ms | 211 ms | **8,406 ms** | 3 (7,239–12,840) |
| PDF 10 pages | 43 | 218 ms | 1 ms | **395 ms** | 5 (386–614) |
| PDF 100 pages | 416 | 2,525 ms | 15 ms | **3,594 ms** | 3 (2,541–3,692) |
| PDF 300 pages | 1,251 | 6,927 ms | 17 ms | **5,774 ms** | 3 (5,336–11,203) |

For text files, most of the time is the database: 1,740 chunk rows took **~1.0–1.2 s** to insert
in **2 SQL statements** (SQLAlchemy batches them). For PDFs, extraction dominates. The GIN index
adds no measurable insert cost: 988–1,416 ms without it and 1,091–1,135 ms with it, across three
runs each.

A first in-process timing said 5,635 ms for the same insert, because `.env` had `DB_ECHO=true`
and every statement was being printed. Standing rule 4, re-learned.

**The upload size limit** — anonymous `POST /documents`:

| | Before the middleware | After |
|---|---|---|
| 50 MB body | 401 after **729 ms** (whole body received) | 413 |
| 200 MB body | 401 after **4,146 ms** | 413 |
| headers only, `Content-Length` 200 MB / 1 GB | — | 413 in **3.2 ms / 1.4 ms**, 0 body bytes read |

**Search** — 20 runs after 3 warm-up, `limit=5`:

| Organisation | Query | `ts_rank_cd` | `ts_rank` |
|---|---|---|---|
| 20 chunks | 3 common words | 22.7 ms | 26.4 ms |
| 39,588 chunks | 3 common words, `any` | **5,909 ms** | **108 ms** |
| 39,588 chunks | 3 common words, `all` | 5,104 ms | 145 ms |

The 39,588-chunk corpus is synthetic, built from a 50-word vocabulary, so ~95–100% of chunks match
any common word. **That is a worst case, not a typical one.**

Query plans (each run twice, second reported):

| Case | Plan | Execution |
|---|---|---|
| small organisation (20 chunks), `refund` | `Index Scan using ix_document_chunks_organization_id` | 0.517 ms |
| large organisation, word in no chunk | `Bitmap Index Scan on ix_document_chunks_search_vector` | 0.356 ms |
| large organisation, `refund` (37,625 of 39,588 match) | `Seq Scan` — GIN index declined | 84.6 ms |

The planner declined the GIN index when 95% of rows matched, for the same reason it declined the
organisation index in v4: an index that selects almost everything saves nothing. With `ts_rank_cd`,
matching took 118 ms and ranking took ~6,070 ms of the 6,187 ms total.

**Retrieval quality** (`scripts/evaluate_retrieval.py`, 20 articles, 30 questions, top 5 documents):

| `match=any`, `ts_rank` | n | hit@1 | hit@3 | hit@5 | MRR | empty |
|---|---|---|---|---|---|---|
| overall | 30 | 0.60 | **0.73** | 0.73 | 0.66 | 0.10 |
| lexical | 15 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 |
| paraphrase | 15 | 0.20 | **0.47** | 0.47 | 0.31 | 0.20 |

`match=all`: overall hit@3 0.40; lexical 0.80; paraphrase **0.00**, all 15 returning nothing.
`ts_rank_cd` (any): hit@1 0.57, hit@3 0.73, MRR 0.64.

Paraphrases that failed: "Can I get my money back?" (nothing), "When can I talk to a human?"
(nothing), "Your website is not loading, is it down for everyone?" (nothing), "How can I make
someone an admin?" (the article says "Administrators"), "I want you to forget everything you know
about me" (the article says "erase"), and three others. Every one is a vocabulary mismatch.

**Caveats, stated rather than hidden:** the articles and questions were written together by the
same author, and the lexical questions were deliberately phrased with the articles' words, so
**1.00 is an upper bound, not an expectation.** With 30 questions, one question is 3.3 percentage
points. These numbers are a baseline for *comparison*, not a claim about real-world accuracy.

**Tests:** 180 passed in 187.08 s (`DB_ECHO=false`).

## Interview Explanation

> The platform needed a knowledge base before any retrieval or RAG work could start, so v6 ingests
> documents — text, Markdown and PDF — splits them into ~800-character overlapping chunks, and
> stores both in PostgreSQL with a generated `tsvector` column and a GIN index. Everything is
> organisation-scoped with the same rules as tickets. I added keyword search as a deliberate
> baseline, and a 30-question evaluation: it finds the right article in the top 3 for every
> question that reuses the article's words, and for fewer than half of the paraphrases. That gap is
> the measured reason to add embeddings next. Measuring found two things I hadn't planned for. The
> ranking function I first chose, `ts_rank_cd`, cost 6 seconds on a large organisation, against
> 108 ms for `ts_rank`, with no loss in evaluation quality. And FastAPI reads an entire multipart
> body before running authentication, so an anonymous 200 MB upload took 4 seconds to be told 401.
> A middleware now refuses it from the Content-Length header in about 2 ms. The remaining problem
> is that ingestion runs inside the request: a 300-page PDF holds a worker for about 6 seconds.

## Next Possible Limitation

Two measured candidates, both of them triggers the roadmap predicted:

1. **Keyword search misses meaning.** Paraphrase hit@3 is 0.47, and 20% of paraphrases return
   nothing. This is the trigger for **embeddings** (v8) and, with them, vector search (v9). The
   evaluation set and results file are ready for the comparison.
2. **Ingestion blocks the request.** A 300-page PDF holds a worker for 5.8 s (P50) and a 4.9 MB
   text file for 8.4 s. With a thread pool of ~40, a handful of simultaneous uploads would crowd
   out every other request. This is the trigger for **background processing** (v7): accept the
   file, return 202, and extract and chunk in a worker. It will be needed even more once embedding
   runs at ingest time.

Also carried forward: search cost grows with the number of matching chunks; originals are not kept;
streamed uploads bypass the size check; the test suite is at 187 s.
