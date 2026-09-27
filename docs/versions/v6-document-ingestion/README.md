# v6 — Document ingestion and keyword search

The platform can now hold knowledge. Upload a help article or PDF and it is extracted, split into
search-sized chunks, stored under your organisation, and findable with keyword search. The search
exists mainly to set the **baseline** that embeddings will have to beat.

![v6 architecture](../../architecture/v6.svg)

## What this version adds

- `POST /documents` — `.txt`, `.md`, `.pdf`, up to 5 MB and 300 pages. Type is checked from the
  name **and** the first bytes. Duplicate files (same SHA-256) are refused with 409 before any
  extraction
- `GET /documents`, `GET /documents/{id}`, `DELETE /documents/{id}` — paged, organisation-scoped,
  404 across tenants
- `GET /documents/search?q=…` — PostgreSQL full-text search over chunks, ranked with `ts_rank`,
  `limit` ≤ 20, `match=any|all`
- ~800-character chunks with 100 characters of overlap, stored in the same transaction as their
  document
- A per-user ingestion budget (20/min), separate from inference
- A middleware that refuses oversized bodies **before** authentication, from `Content-Length` alone
- A 30-question retrieval evaluation over 20 help articles

## Run

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
uvicorn app.main:app
```

## Try it

With a `$TOKEN` from `POST /auth/login`:

```bash
curl -X POST http://127.0.0.1:8000/documents -H "Authorization: Bearer $TOKEN" -F "file=@evaluation/knowledge_base/refund-policy.md"
```

```bash
curl -G http://127.0.0.1:8000/documents/search -H "Authorization: Bearer $TOKEN" --data-urlencode "q=how long does a refund take"
```

```bash
curl -G http://127.0.0.1:8000/documents/search -H "Authorization: Bearer $TOKEN" --data-urlencode "q=can I get my money back"
```

The second search finds the refund policy. The third finds nothing: the article says "refund",
never "money back". That is what keyword search can't do, and it's the reason for v8.

## Test and evaluate

```bash
pytest -v
```

```bash
python scripts/evaluate_retrieval.py
```

180 tests. Worth reading: `test_a_failed_chunk_insert_leaves_no_document_behind` (a document and
its chunks are written in one transaction, and a NOT NULL violation is reported as 503, not
mistaken for a duplicate), and `test_an_oversized_body_is_refused_before_authentication`.

## Results (measured locally, one session)

| | |
|---|---|
| Keyword search, hit@3 — questions using the article's words | **1.00** (an upper bound: written by the same author) |
| Keyword search, hit@3 — paraphrased questions | **0.47**; 20% return nothing |
| Upload, 10 KB text / 1 MB text / 4.9 MB text | 165 ms / 2,372 ms / **8,406 ms** P50 |
| Upload, 10 / 100 / 300-page PDF | 395 ms / 3,594 ms / **5,774 ms** P50 |
| Search, 39,588 chunks, common words: `ts_rank_cd` → `ts_rank` | **5,909 ms → 108 ms**, same evaluation quality |
| Anonymous 200 MB upload | 401 after 4,146 ms → **413 in 1.4–3.2 ms** |

## Documents

- Full version document: [v6-document-ingestion.md](../v6-document-ingestion.md)
- [ADR-018 — Chunks in PostgreSQL with full-text search as the retrieval baseline](../../adr/ADR-018-postgres-full-text-search-baseline.md)
- [ADR-019 — Store extracted text, not original files](../../adr/ADR-019-store-extracted-text-not-originals.md)
- [ADR-020 — Refuse oversized request bodies before authentication](../../adr/ADR-020-body-size-limit-before-auth.md)
- Previous version: [v5 — Rate limiting](../v5-rate-limiting.md)
