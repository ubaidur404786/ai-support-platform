# ADR-025: Search vectors inside PostgreSQL with pgvector and an HNSW index

Status: accepted (v9). Replaces [ADR-024](ADR-024-brute-force-vector-search-in-numpy.md).

## Context

v8 stored every embedding as 1,536 raw bytes and searched by brute force. It read every vector of
the organisation into Python and scored them all with NumPy. That search was exact, but its cost
grew with the size of the organisation:

| Chunks in the organisation | v8 semantic search, P50 through the API |
|---|---|
| 1,000 | 825 ms |
| 10,000 | 4,428 ms |
| 50,000 | **10,498 ms** |

At 50,000 chunks, reading the vectors took 13,008 ms and the maths took 36 ms. The problem was
**moving 77 MB out of the database for every question**, not the comparison itself.

## Options

1. **Keep brute force, make the copy faster** (cache vectors in the API's memory). Fast after the
   first question, but every API process would hold every organisation's vectors, and the cache
   goes stale on every upload.
2. **pgvector without an index.** A `vector` column, with the distance computed in SQL
   (`ORDER BY embedding <=> :question LIMIT 5`). Still exact, and only 5 rows leave the database.
   PostgreSQL still reads every row.
3. **pgvector with an IVFFlat index.** It groups the vectors into lists and searches only the
   closest lists. It has to be built after the data exists, and its quality drops as new rows
   arrive, unless it is rebuilt.
4. **pgvector with an HNSW index.** A graph linking each vector to its near neighbours. The search
   walks the graph towards the question. It can be built on an empty table and stays correct as
   rows are added.
5. **A separate vector database** (Qdrant, Weaviate, Milvus).

## Decision

Option 4: a `vector(384)` column and an HNSW index built for cosine distance
(`vector_cosine_ops`, `m=16`, `ef_construction=64`). Each search runs with two per-transaction
settings: `hnsw.iterative_scan = strict_order` and `hnsw.ef_search = 100`.

## Why?

Measured on the same laptop, in the same session, on the same 50,000 synthetic chunks:

| Method, in-process (no HTTP) | P50 |
|---|---|
| v8: read every vector, NumPy (5 questions) | 6,476 ms |
| Option 2: exact, inside PostgreSQL | 1,829–2,236 ms |
| **Option 4: HNSW index**, `ef_search` 40–200 | **72–95 ms** |

Through the API, with the question embedding included, semantic search at 50,000 chunks went from
**10,498 ms (v8) to 147 ms (v9)**, P50. That is about the same as keyword search (149 ms).

- **Option 2 alone was not enough.** Computing in the database removed the data movement (3.5×
  faster), but it still reads every row, so its cost still grows with the organisation.
- **HNSW over IVFFlat**: the worker adds chunks all the time. HNSW needs no rebuild and no "build it
  once the data exists" step, which suits a table that starts empty in every new deployment.
- **Option 5 would add a second datastore.** It would need its own backups, its own copy of the
  tenant filter, and a way to keep it in step with `document_chunks`, where the text already lives.
  PostgreSQL does the job at this size, and the `organization_id` filter stays in the same SQL
  statement as the vector search.

**Approximate means it can miss.** HNSW does not look at every vector. Recall@5 is the share of the
true 5 nearest chunks that the index also returns (`scripts/measure_vector_index.py` and a one-off
real-text run):

| Data | `ef_search=40` (default) | `100` (chosen) | `200` |
|---|---|---|---|
| 12,000 **real** text paragraphs, embedded by the real model, 100 held-out paragraphs as questions | 0.98 | **0.99** | 1.00 |
| 50,000 random vectors, questions near a stored chunk: true nearest found | 0.82 | **0.96** | 1.00 |
| 50,000 random vectors, random questions (no structure, worst case) | 0.13 | 0.27 | 0.42 |

On real text the index is almost exact. On random vectors it is poor. Random points in 384
dimensions are all about equally far apart, so "the 5 nearest" hardly means anything, and a graph
cannot find them. `ef_search=100` costs the same time as 40 here (~60–80 ms) and removes most of
the misses, so it is set explicitly.

**The filtering trap.** By default, an HNSW scan collects `ef_search` candidates and only then
applies `WHERE organization_id = …`. When one organisation's chunks fill the neighbourhood of a
question, a small organisation gets **zero** results. `iterative_scan = strict_order` makes the
scan continue until enough rows pass the filter. This is proved by
`test_a_small_organisation_still_gets_its_results_from_the_index`, which fails (`0 == 3`) with
that line removed.

## Trade-offs

Gain:

- Search time no longer grows with the organisation: 147 ms at 50,000 chunks through the API.
- The distance is computed in SQL. Only the `limit` best rows leave the database, and v8's second
  query for the text is gone.
- The column is typed: pgvector refuses a vector of the wrong size on insert.

Lose:

- **Results are approximate.** Recall has to be measured on real data. It cannot be assumed.
- **A 98 MB index** next to a 245 MB table (50,000 chunks).
- **Slower writes.** Every inserted chunk updates the graph: 12,000 chunks took 143 s to insert,
  though embedding them takes far longer (1–10 chunks/s). Building the index over 50,000 existing
  rows took 137 s. PostgreSQL's default `maintenance_work_mem` (64 MB) ran out after 28,364 rows,
  and the rest of the build was slower.
- **A database extension**: the PostgreSQL image must include pgvector ([ADR-026](ADR-026-build-pgvector-into-the-alpine-image.md)).
- **The planner decides.** On small organisations PostgreSQL skips the index and compares every row,
  which is exact and fast at that size (12,000 real chunks: ~150 ms). Tests that need the index
  have to force it (`enable_sort = off`).
- **The size is fixed at 384.** A model with a different vector size needs a migration, not just
  a new `EMBEDDING_MODEL`.

## Future Trigger

- **Recall on the organisation's real data drops below ~0.95**: raise `ef_search`, or rebuild with a
  larger `m` / `ef_construction`.
- **Index builds or memory become the bottleneck** (millions of chunks): raise
  `maintenance_work_mem` for builds, use `halfvec` (16-bit numbers, half the size), or partition
  by organisation.
- **Vector search load starts competing with the transactional workload** on one PostgreSQL: a read
  replica first, and a dedicated vector database only if that is not enough.
