# v7 — Background processing for document ingestion

Uploading a document no longer makes you, or anyone else, wait. The upload stores the file and
answers **202 "queued"** in milliseconds. A separate **worker process** extracts and chunks it,
then marks it `ready`. PostgreSQL is the queue, so there is no new service to run.

![v7 architecture](../../architecture/v7.svg)

## What this version adds

- `POST /documents` → **202** with `"status": "queued"`. Type (415), size (413), duplicates (409)
  and a full queue (**429**, 50 waiting documents per organisation) are still answered at once
- `status` / `error_message` / `processed_at` on every document: `queued → processing → ready | failed`
- `python -m app.worker`: claims documents with `FOR UPDATE SKIP LOCKED`, retries unexpected
  errors 3 times, fails unreadable files at once with a reason, and rescues documents abandoned by
  a crashed worker after 5 minutes
- `document_files`: uploaded bytes wait here, and only until they have been processed
- `documents.content` is no longer loaded when listing (it was: up to 5 MB per row)

## Run

```bash
docker compose up -d
```

```bash
alembic upgrade head
```

```bash
uvicorn app.main:app
```

In a second terminal:

```bash
python -m app.worker
```

## Try it

```bash
curl -X POST http://127.0.0.1:8000/documents -H "Authorization: Bearer $TOKEN" -F "file=@evaluation/knowledge_base/refund-policy.md"
```

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/documents/1
```

The first answer says `"status": "queued"`. A second later the worker logs
`Document 1 ready: N chunks` and the second command shows `"status": "ready"`. Stop the worker and
upload again: the document stays `queued` until you start the worker again. Nothing is lost.

## Test

```bash
pytest -v
```

Worth reading in `tests/test_worker.py`: `test_two_workers_never_take_the_same_document`
(SKIP LOCKED), `test_a_failure_while_saving_leaves_no_chunks_and_retries`,
`test_a_worker_that_was_replaced_throws_its_result_away`, and
`test_the_worker_process_knows_every_table_it_writes` (a bug only a separate process could have).

## Results (measured locally, laptop on battery, two rounds per version, P50)

| | v6 | v7 |
|---|---|---|
| Upload answers, 300-page PDF | 7,848–8,144 ms | **154–217 ms** |
| Upload answers, 4.9 MB text | 10,324–11,343 ms | **775–854 ms** |
| Document ready to search, 300-page PDF | same as the answer | 8,518–9,543 ms (unchanged work) |
| `GET /health` P50 while 4 PDFs are processed (quiet ≈ 15 ms) | 335–729 ms | **17–21 ms** |
| `GET /health` P95, same load | 2,220–2,979 ms | **34–134 ms** |
| List query, 5 rows with 4.9 MB documents (in-process) | 153.5 ms | **3.4 ms** |

## Documents

- Full version document: [v7-async-processing.md](../v7-async-processing.md)
- [ADR-021 — Use the documents table as the job queue](../../adr/ADR-021-postgres-table-as-job-queue.md)
- [ADR-022 — Keep waiting files in PostgreSQL until they are processed](../../adr/ADR-022-waiting-files-in-postgres.md)
- Previous version: [v6 — Document ingestion](../v6-document-ingestion.md)
