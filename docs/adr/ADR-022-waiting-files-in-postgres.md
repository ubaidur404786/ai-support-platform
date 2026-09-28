# ADR-022 — Keep waiting files in PostgreSQL until they are processed

Status: accepted (v7). Answers the v7 trigger recorded in ADR-019.

## Context

ADR-019 (v6) decided not to keep uploaded files: the text was extracted inside the request and
the bytes were thrown away. It named v7 as the moment to revisit that: once processing moves to a
worker, the bytes must wait somewhere between the upload and the worker. ADR-019 expected that
place to be object storage.

## Options

1. **A `document_files` table in PostgreSQL** (`bytea`), one row per queued document, deleted when
   processing finishes.
2. **Object storage** (MinIO locally, S3-compatible later).
3. **The local filesystem**, with a path in the row.
4. **Inside the queue message** (only possible with an external queue).

## Decision

Option 1. `DocumentFile(document_id, data)` is written in the same transaction as its `Document`
row. The worker reads it, extracts the text, and deletes it in the same transaction that marks the
document `ready` or `failed`.

## Why?

- **ADR-019's objection to `bytea` was that the bytes would be stored forever.** They no longer
  are. A file lives for the seconds between upload and processing, so the table holds only the
  queue. With the worker idle, it is empty.
- **One transaction.** A document is never queued without its file, and a file never outlives its
  document: `ON DELETE CASCADE` removes it if the owner deletes a queued document. MinIO plus
  PostgreSQL has no shared transaction, so it needs write ordering and a cleanup job for orphans.
- **The size is bounded.** Uploads are at most 5 MB, and each organisation has at most 50 waiting
  documents (`MAX_PENDING_DOCUMENTS_PER_ORGANIZATION`), so the worst case per organisation is
  250 MB, briefly.
- **The local filesystem (3) fails as soon as the worker runs on another machine**, which is the
  point of having a separate worker.
- **A separate table, not a column on `documents`**, so listing documents never reads file bytes.
  A v7 measurement showed what reading unneeded large columns costs: listing 5 documents took
  ~200 ms while the 4.9 MB `content` column was loaded with every row. `content` is now deferred.

## Trade-offs

Gain: no second storage system; the file, the document and the job are consistent by construction.

Lose:

- **Originals are still not kept** after processing (ADR-019's losses stand: no re-extraction, no
  download).
- **Large values pass through the database** twice per upload, once written and once read. At a
  few uploads a minute this is invisible. At high volume it is traffic the database should not
  carry.

## Future Trigger

- **Keeping originals** (re-extraction with OCR or a better parser, "download original") → object
  storage. The bytes would then be permanent, and ADR-019's objection to `bytea` applies again.
- **Larger files** (the 5 MB limit raised by an order of magnitude or more) → upload straight to
  object storage (presigned URLs) so that large bodies never pass through the API.
