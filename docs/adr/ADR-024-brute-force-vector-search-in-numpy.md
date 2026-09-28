# ADR-024: Store vectors as bytes in PostgreSQL and compare them all in NumPy

Status: accepted (v8). Expected to be replaced in v9. The measurement below is the trigger.

## Context

Every chunk now has an embedding of 384 numbers (ADR-023). A semantic search has to find the chunks
whose vectors are closest to the question's. That raises two questions: where the vectors are
stored, and how the closest ones are found.

## Options

1. **A `bytea` column plus brute force in Python.** The 384 float32 numbers are packed into
   1,536 bytes. A search reads every vector of the organisation, scores them all with one NumPy
   matrix product, and keeps the best.
2. **A `REAL[]` (array) column plus brute force.** The same approach, but readable in `psql`.
   Every number is converted to a Python float on the way out, which is 384 conversions per chunk.
3. **pgvector**: a PostgreSQL extension with a `vector` type, distance operators, and indexes
   built for vectors (HNSW, IVFFlat). The search runs inside the database, and only the top results
   leave it.
4. **A separate vector database** (Qdrant, Weaviate, Milvus).

## Decision

Option 1 in v8, measured on purpose.

## Why?

- **It isolates one question.** v8's job is to answer "do embeddings find better answers?", and
  they do: hit@3 went from 0.73 to 0.93. Adding pgvector at the same time would change the Docker
  image, the column type, and the search engine all at once, in the same version that first adds
  embeddings.
- **It is exact.** Brute force compares against every chunk, so it can never miss the true nearest
  neighbour. That makes it the correct baseline for judging an approximate index later.
- **It is easy to follow.** One `SELECT`, one `np.frombuffer`, one `matrix @ query`, and one
  `argsort`, all in `PostgresDocumentRepository.semantic_search`.
- **bytea, not `REAL[]`**, because bytes turn back into a NumPy array with a single memory copy. An
  array column would make every number a Python object first, which penalises the brute-force
  approach unfairly.
- **Option 4 adds a second datastore.** It has its own consistency, backups and tenancy filter, for
  a scale that has not been reached yet.

## Measured result (the reason this will not last)

Search latency through the API, `limit=5`, 30 requests per mode. The organisation was filled with
synthetic chunks and random normalised vectors: brute force does the same work whatever the
values are. The laptop was plugged in but heavily loaded:

| Chunks in the organisation | keyword P50 | semantic P50 | semantic P95 |
|---|---|---|---|
| 1,000 | 65 ms | 825 ms | 1,999 ms |
| 10,000 | 94 ms | 4,428 ms | 6,337 ms |
| 50,000 | 152 ms | **10,498 ms** | 25,589 ms |

The same query at 50,000 chunks, timed piece by piece in-process (P50 of 5):

| Step | Time |
|---|---|
| Read 50,000 vectors (77 MB) from PostgreSQL | **13,008 ms** |
| Join the bytes into one NumPy array | 630 ms |
| Score all 50,000 and sort | 36 ms |

The maths is not the problem: it takes 36 ms. Moving every vector out of the database on every
question is. The cost grows with the size of the organisation, not with `limit`. It is v3's
unbounded list again, this time inside a search.

On the evaluation knowledge base (20 chunks), semantic search answered in 627 ms P50, against
64 ms for keyword search. At that size, most of the time goes on embedding the question (141 ms
in-process) and on CPU contention with the rest of the machine.

## Trade-offs

Gain: exact results, no new infrastructure, and the simplest code that can show embeddings work.

Lose: latency that grows linearly with the number of chunks, 1.5 KB of `bytea` per chunk (77 MB
per 50,000 chunks), and every vector held briefly in the API process's memory on every search.

## Future Trigger

**Already fired.** An organisation with 10,000 chunks, about eight 300-page manuals, waits more
than 4 seconds per semantic search. That is v9-vector-search:

1. pgvector, with the distance computed inside PostgreSQL, so only the top `limit` rows leave the
   database.
2. An approximate index (HNSW), measured against this exact baseline for both latency and recall.
   An index that is fast and wrong is not an improvement.
