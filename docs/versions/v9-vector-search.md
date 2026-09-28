# v9 — Vector search inside PostgreSQL (pgvector + HNSW)

API 0.9.0 · classifier model v0.1.0 unchanged · embedding model `sentence-transformers/all-MiniLM-L6-v2` unchanged · pgvector 0.8.6 · 209 tests passing (373 s)

![v9 architecture](../architecture/v9.svg)

## Recap: where v8 left us

Acme's knowledge base has grown to 50,000 chunks: product manuals, policies, and two years of
release notes. Priya types *"Can I get my money back?"* with `mode=semantic`. v8 finds the refund
policy, because it compares meaning, but the answer takes **ten seconds** to arrive. Every question
copies all 50,000 vectors (77 MB) out of PostgreSQL into the API, only to keep 5 of them.

What broke, in one sentence: semantic search took **10,498 ms P50 at 50,000 chunks**, and 13,008 ms of
a 13.7 s in-process run was reading vectors, while the maths took 36 ms.

What v8 changed: the worker embeds every chunk (`embeddings.py`, fastembed, CPU), and
`repository.semantic_search` scores them all with NumPy. Paraphrase hit@3 went from 0.47 to 0.87.

What was still wrong, and became v9: the cost of that search grew with the size of the organisation,
not with `limit`.

## Problem

| Chunks | v8 semantic P50 (API) | keyword P50 |
|---|---|---|
| 1,000 | 825 ms | 65 ms |
| 10,000 | 4,428 ms | 94 ms |
| 50,000 | **10,498 ms** | 152 ms |

The time goes into moving data, not into the comparison. A bigger machine would not change that
shape: the work per question is proportional to the number of chunks the organisation owns.

## Current Architecture

```
GET /documents/search?mode=semantic
  → service: embed the question (~141 ms)
  → repository.semantic_search:
       SELECT id, embedding FROM document_chunks … WHERE organization_id = …   (every row, bytea)
       NumPy: matrix (n × 384) @ question  → best `limit`
       second SELECT: text + title of the winners
```

## Why the Old Design Is Not Enough

Brute force has two costs: reading every vector, and comparing every vector. v8 measured that the
first is 360 times the second. Doing the comparison **where the data is** removes the reading. Not
comparing every vector at all (an index) removes the growth. Caching vectors in the API would make
each process hold every organisation's vectors, and the cache would go stale on every upload.

## Solution

**pgvector**, a PostgreSQL extension, adds a `vector` column type, distance operators, and indexes
built for vectors. **HNSW** ("hierarchical navigable small world") is one such index: a graph in
which every vector is linked to its near neighbours. A search enters the graph and keeps stepping to
whichever neighbour is closer to the question, so it looks at a few hundred vectors instead of
50,000. Because it does not look at everything, it is **approximate**: it can miss a true nearest
neighbour. How often it misses is **recall**, and it is measured below.

```
SEARCH (API process)
  GET /documents/search?q=Can I get my money back?&mode=semantic
    ▼ service.search             embed the question (unchanged)
    ▼ repository.semantic_search ONE query:
         SET LOCAL hnsw.iterative_scan = strict_order     (see "the filtering trap")
         SET LOCAL hnsw.ef_search = 100                   (see "recall")
         SELECT document_id, title, chunk_index, text, embedding <=> :question AS distance
           FROM document_chunks JOIN documents …
          WHERE organization_id = :org AND documents.embedding_model = :model
          ORDER BY distance LIMIT 5
    ▼ PostgreSQL walks ix_document_chunks_embedding (HNSW, cosine) → 5 rows
  200 {"mode": "semantic", "results": [{"rank": 0.52, …}]}      rank = 1 − distance, as in v8

INGEST (worker): unchanged, except that the vector goes into a vector(384) column,
and every insert also adds the chunk to the HNSW graph.
```

Files:

| File | What changed |
|---|---|
| `docker/postgres/Dockerfile` | **new**: `postgres:17-alpine` + pgvector 0.8.6, compiled in the image |
| `docker-compose.yml` | builds that image (`ai-support-db:pg17-pgvector`); same data volume |
| `alembic/versions/e5f1a8c3d9b2_...py` | `CREATE EXTENSION vector`; converts `embedding` from bytea to `vector(384)` (exact copy, 1,000 rows at a time); builds the HNSW index. Downgrade converts back |
| `app/documents/models.py` | `embedding: vector(384)`; the HNSW index declared on the model (so `alembic check` stays clean) |
| `app/documents/embeddings.py` | `EMBEDDING_DIMENSIONS = 384`; `to_bytes`/`from_bytes` removed |
| `app/documents/processing.py` | writes the NumPy vector directly |
| `app/documents/repository.py` | `semantic_search` is one SQL query; `HNSW_EF_SEARCH = 100` |
| `tests/test_vector_search.py` | **new**: the query can use the index; a small organisation still gets results from it |
| `scripts/measure_vector_index.py` | **new**: exact vs index latency and recall, for `ef_search` 40/100/200 |
| `requirements.in`, `requirements.txt` | `pgvector` (lets SQLAlchemy read and write `vector` columns) |

## Why This Solution?

**Why pgvector and HNSW?** ([ADR-025](../adr/ADR-025-pgvector-hnsw-index.md)) The vectors, the
text and the tenant filter stay in one database and one SQL statement. There is no second datastore
to back up or keep in step. HNSW needs no rebuild as the worker keeps adding chunks, unlike IVFFlat.

**Why build our own image?** ([ADR-026](../adr/ADR-026-build-pgvector-into-the-alpine-image.md))
The ready-made `pgvector/pgvector` image is Debian-based, and ours is Alpine. The two sort text
differently, so opening the existing volume with the other image could silently break every index
on a text column. Compiling pgvector into the Alpine image we already run leaves the data untouched.

**Why cosine (`<=>`, `vector_cosine_ops`)?** It is the same score v8 returned (`rank = 1 −
distance`), so the API response and every test threshold keep their meaning. The index only
serves the operator it was built for. `test_the_search_query_can_use_the_vector_index` fails if the
query sorts by another one (checked with `<->`).

**Why `ef_search = 100`, not the default 40?** On real text the default already found 98% of the
true top 5. 100 found 99% at the same ~60 ms, and on the hardest data it doubled recall (0.13 →
0.27) for no measurable cost.

**Why an iterative scan: the filtering trap.** An HNSW scan normally collects `ef_search`
candidates **first** and applies `WHERE organization_id = …` **afterwards**. If a large organisation
owns every vector near the question, a small organisation's search gets **zero rows**, although it
has chunks. Isolation still holds (no other tenant's data is returned), but results go missing.
`hnsw.iterative_scan = strict_order` keeps the scan going until enough rows pass the filter. The
test for this fails with `0 == 3` when the line is removed.

**Why are the new tests at repository level?** On a table of a few rows, PostgreSQL never uses the
index. Reading everything is cheaper. The API tests therefore pass whether the index works or not.
The new tests force the index (`SET LOCAL enable_sort = off`) and check, with
`pg_stat_get_xact_numscans`, that it was really used.

### Failure-first: what happens when...

| Situation | What happens | Proved by |
|---|---|---|
| The query sorts by an operator the index was not built for | PostgreSQL silently reads every row: correct results, v8 speed | `test_the_search_query_can_use_the_vector_index` (verified to fail with `<->`) |
| A small organisation shares the index with a large one | Iterative scan: it still gets its own results | `test_a_small_organisation_still_gets_its_results_from_the_index` (verified to fail without the setting) |
| The index misses a true neighbour | A slightly worse result is returned; recall measured 0.99 on real text | `scripts/measure_vector_index.py` |
| The extension is missing from the database server | `alembic upgrade` fails at `CREATE EXTENSION vector`, before any data is touched | the migration runs in one transaction |
| A vector with the wrong number of values | PostgreSQL refuses the insert (`expected 384 dimensions`) | the column type |
| Downgrading to v8 | The migration converts every vector back to the same bytes and drops the extension | round trip on 1,500 vectors: identical (`np.array_equal`), NULLs kept |
| The model is unavailable | Unchanged from v8: 503 "try mode=keyword" | `test_an_unavailable_model_is_503_and_keyword_search_still_works` |

## New Trade-offs

- **Approximate results.** The index can miss a nearest chunk. On real text, recall@5 was 0.99.
  On random vectors it was 0.27. Recall depends on the data, so it has to be re-measured on real
  organisations, not assumed from this benchmark.
- **Storage.** The index is 98 MB next to a 245 MB table at 50,000 chunks.
- **Slower writes and builds.** Inserting 12,000 chunks with the index in place took 143 s. That is
  small next to the embedding itself (1–10 chunks/s), but it is not free. Building the index over
  50,000 existing rows took 137 s. PostgreSQL's default `maintenance_work_mem` (64 MB) ran out after
  28,364 rows, and the rest of the build was slower.
- **The planner decides, not the code.** For a small organisation PostgreSQL skips the index and
  compares every row, which is exact and fast at that size. The code cannot say "use the index",
  so tests that need it have to force it.
- **A custom database image.** It is built on first `docker compose up`, which needs network
  access to GitHub, and we now own the pgvector upgrade.
- **The vector size is part of the schema.** A model with 768 numbers needs a migration, not only a
  new `EMBEDDING_MODEL`.

## What Changed

- Semantic search runs inside PostgreSQL through an HNSW index. The API and its response are
  unchanged.
- `document_chunks.embedding` is `vector(384)`. The migration converts existing vectors exactly and
  is reversible.
- PostgreSQL image: Alpine plus pgvector, built from `docker/postgres/Dockerfile`.
- Two repository-level tests; a measurement script for recall and latency.
- **Found by a test, not planned:** the filtering trap. Without the iterative scan, a small
  organisation sharing the index with a large one received no results.
- **Found by measuring:** recall on random vectors is poor (0.13 at the default `ef_search`). A
  benchmark on synthetic data alone would have looked like a broken index. The real-text run shows
  it is not.

## How to Test

```bash
docker compose up -d --build db
pip install -r requirements.txt
alembic upgrade head
pytest -v
```

```bash
pytest tests/test_vector_search.py -v
```

Search, as in v8 (the API did not change):

```bash
curl -G http://127.0.0.1:8000/documents/search -H "Authorization: Bearer $TOKEN" --data-urlencode "q=Can I get my money back?" -d mode=semantic
```

Measure (with `DB_ECHO=false`), against a database with a large organisation, such as the one
`scripts/measure_semantic_search.py` fills:

```bash
python scripts/measure_semantic_search.py
python scripts/measure_vector_index.py --organization-id 3
```

## Expected Result

- `docker exec ai-support-db psql -U support -d support_platform -c "\dx"` lists `vector 0.8.6`.
- `alembic upgrade head` prints `Running upgrade d7a2c4e9b1f5 -> e5f1a8c3d9b2`.
- The two tests in `tests/test_vector_search.py` pass.
- `measure_vector_index.py` prints `index used: yes` for the `ef_search` rows, and `no` for exact.

## Measurements

Local Windows 11 laptop, PostgreSQL 17 in Docker, `DB_ECHO=false`, no `--reload`, Python client.
**The machine was under memory pressure** (often 100–800 MB of 7.8 GB free). During the session
the Docker VM stopped once, and one embedding run died with an out-of-memory error. It was partly on
battery. All v9 numbers and the in-process v8 number were taken in the same session. The v8 API
latencies come from the v8 session.

**1. Latency at 50,000 chunks** (synthetic chunks with random normalised vectors, `limit=5`):

| Method | P50 | P95 |
|---|---|---|
| v8 brute force, in-process (this session, 5 questions after 1 warm-up) | 6,476 ms | — |
| Exact inside PostgreSQL, index switched off (in-process, 50 questions) | 1,829–2,236 ms | 2,398–2,583 ms |
| **HNSW index**, `ef_search` 40 / 100 / 200 (in-process, 50 questions) | **72–95 ms** | 82–1,010 ms |
| v8, through the API (v8 session) | 10,498 ms | 25,589 ms |
| **v9, through the API**, including embedding the question (30 requests) | **147 ms** | **319 ms** |
| keyword, through the API, same run | 149 ms | 201 ms |

The first semantic search after the API started took 10.8 s (it loads the model, as in v8).

**2. Recall: does the index return what exact search returns?** `recall@5` = share of the exact top
5 that the index also returned. `nearest` = how often the single closest chunk was among them.

| Data (questions) | `ef_search` | recall@5 | nearest | P50 |
|---|---|---|---|---|
| **12,000 real paragraphs** (100 held-out paragraphs) | 40 | 0.98 | 0.98 | 59 ms |
| | **100** | **0.99** | **0.99** | 61 ms |
| | 200 | 1.00 | 1.00 | 66 ms |
| | exact | — | — | 148 ms |
| 50,000 random vectors (50 questions near a stored chunk) | 40 | 0.39 | 0.82 | 88 ms |
| | **100** | 0.48 | **0.96** | 73 ms |
| | 200 | 0.57 | 1.00 | 76 ms |
| 50,000 random vectors (50 random questions, worst case) | 40 | 0.13 | 0.22 | 95 ms |
| | **100** | 0.27 | 0.36 | 76 ms |
| | 200 | 0.42 | 0.50 | 80 ms |

The real-text set is 12,000 English paragraphs (library docstrings from the local Python install,
80–400 characters) plus the 20 evaluation articles, embedded by the real model. It was a one-off
run; the script is not in the repository. At 12,000 chunks the planner chose exact search on its
own (148 ms), so the index rows were forced with `enable_sort = off`. Random vectors in 384
dimensions are all roughly the same distance apart. "The 5 nearest" barely means anything there,
which is why recall@5 is low even when the nearest chunk is found.

The 30 evaluation questions scored the same with exact search and the index (hit@3 0.73–0.77) in
the real-text set. Surrounded by 12,000 unrelated paragraphs, exact search itself drops from 0.93 to
0.73, because some distractors (e.g. docstrings about passwords) outrank the articles. That is a
retrieval-quality issue, not an index issue, and it is one more reason for a relevance threshold
and hybrid retrieval.

A caution from a discarded experiment: 20 real article vectors hidden among 50,000 random vectors
gave hit@3 0.90 exact but **0.37** through the index at `ef_search=40` (0.70 at 100). Real vectors
sit in a region of space that random vectors never reach, so the graph barely connects to them.
Real knowledge bases do not look like that, but it shows why recall is measured on real data.

**3. The cost of the index:**

| | Measured |
|---|---|
| Index size / table size, 50,000 chunks | 98 MB / 245 MB |
| Build over 50,000 existing rows (`REINDEX`) | 137 s (`maintenance_work_mem` 64 MB exceeded at 28,364 rows) |
| Whole migration of the 50,000-chunk database (convert + build) | 4 min 1 s |
| Insert 12,000 chunks with the index in place | 143 s |
| Building the PostgreSQL image (compile pgvector) | ~165 s, once |

## Interview Explanation

"In v8 I used exact brute-force vector search on purpose, as a baseline, and measured it: at 50,000
chunks a question took about 10 seconds, and almost all of that was copying vectors out of Postgres,
not the maths. In v9 I moved the search into the database with pgvector, and added an HNSW index,
a graph of nearest neighbours that the search walks instead of scanning everything. Through the API
that went from 10.5 seconds to about 150 milliseconds, the same as keyword search. Because HNSW is
approximate, I measured recall against the exact baseline. On real text it returned 99% of the
true top 5. On random vectors it was much worse, which taught me to measure recall on realistic
data. A test also caught a real bug: HNSW applies the tenant filter after collecting candidates, so
a small organisation sharing the index with a big one got zero results. Pgvector's iterative scan
fixes that, and the test fails without it. I kept Postgres instead of adding a vector database,
because the text, the vectors and the tenant filter stay in one query. I also compiled pgvector into
our existing Alpine image instead of switching to the Debian-based one, because the two C libraries
sort text differently and could have silently broken the text indexes on the existing volume."

## Next Possible Limitation

- **No "nothing relevant".** Retrieval always returns 5 chunks, however unrelated (v8 test: score
  < 0.3 for "What is the capital of France?"). Before generated answers (v10-rag) use retrieved
  chunks as facts, a relevance threshold is needed, chosen by measuring answerable against
  unanswerable questions.
- **Embedding throughput** (1–10 chunks/s here) still decides how fast documents become searchable.
- **Recall is data-dependent.** The benchmark here is not a guarantee for a real customer's
  knowledge base.
- **Unrelated text lowers exact retrieval quality too** (0.93 → 0.73 among 12,000 distractors),
  which calls for hybrid retrieval (v11) and reranking (v12).
