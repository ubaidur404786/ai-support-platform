# Tests

Each test file starts with the version that added it, so reading them in order follows the story of the project.

| File | Version | What it checks |
|---|---|---|
| `test_v00_classifier.py` | v0 | the ticket classifier gives a team and a confidence |
| `test_v00_api.py` | v0 | `/health` and `/classify` over HTTP |
| `test_v01_ticket_service.py` | v1 | business rules for tickets (low confidence → needs review) |
| `test_v01_tickets_api.py` | v1–v4 | create and list tickets, paging (v3), only your organisation's tickets (v4) |
| `test_v04_auth_api.py` | v4 | register, log in, tokens, wrong passwords |
| `test_v05_rate_limit.py` | v5 | too many requests → 429 with `Retry-After` |
| `test_v06_documents_api.py` | v6–v8 | upload, list, keyword and semantic search |
| `test_v06_ingestion_pipeline.py` | v6 | read text, split into chunks |
| `test_v07_worker.py` | v7 | the background worker: queue, retries, a crashed worker |
| `test_v09_vector_search.py` | v9 | pgvector search uses the index and stays inside one organisation |
| `test_v10_answers_api.py` | v10–v12 | answers from documents, "not found", streaming, model service down or busy |
| `test_v12_model_service.py` | v12 | the model service's queue limit |
| `test_v13_embedding_service.py` | v13 | the embedding service, and what happens when it is down |
| `test_v14_health_services.py` | v14 | `/health` reports the other services |
| `test_v16_redis_rate_limit.py` | v16 | rate limits shared by several API copies |

Shared helpers: `conftest.py` (test database, logged-in clients, the embedding service in memory), `fakes.py`, `pdf_factory.py`.

## Run

The tests need PostgreSQL: run `docker compose up -d db` first.

```bash
pytest                                  # everything
pytest tests/test_v10_answers_api.py    # one version's tests
pytest -k "busy"                        # tests whose name contains "busy"
```
