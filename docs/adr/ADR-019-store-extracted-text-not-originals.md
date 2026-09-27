# ADR-019 — Store the extracted text, not the original file

Status: accepted (v6)

## Context

An uploaded document arrives as bytes: a PDF, a Markdown file. After extraction we have plain text
and chunks. The question is whether to keep the original bytes as well, and where.

## Options

1. **Keep only the extracted text** (`documents.content`) and the chunks.
2. Keep the original in PostgreSQL as `bytea`.
3. Keep the original on the local filesystem, with its path stored in the row.
4. Keep the original in object storage (MinIO locally; S3-compatible later).

## Decision

Option 1. The original bytes are hashed (SHA-256, stored for duplicate detection) and then
discarded. `documents.content` holds the full normalised text.

## Why?

- **Everything v6 needs comes from the text.** Search, chunking, and re-chunking with a different
  size later all work from `content`.
- **Options 3 and 4 add a second storage system with no shared transaction.** A file written to
  disk or MinIO and a row written to PostgreSQL can disagree after a crash: there can be a row with
  no file, or a file with no row. Handling that (write order, cleanup jobs) is real engineering,
  and nothing in v6 needs it yet.
- **Option 2 keeps one transaction, but stores up to 5 MB per row** that no feature reads, which
  bloats backups and the table for nothing.
- **Local disk (3) breaks the moment there are two machines**, and v2 already moved state out of
  the process for exactly that reason.

## Trade-offs

Gain: one system of record; the document, its text and its chunks are all written in one
transaction; no orphaned files.

Lose:

- **Re-extraction is impossible.** If a better PDF extractor or OCR arrives, existing documents
  cannot be re-processed. They would have to be uploaded again.
- **No download of the original.** A user can't get back the file they uploaded.
- **Layout is gone.** Tables, images and page structure beyond what pypdf extracts as text are
  lost.

## Future Trigger

- **v7 background processing:** a worker needs the file after the request has returned. The bytes
  must live somewhere between upload and processing. Object storage (option 4) is then a real
  requirement, not a speculative one.
- **A better extractor or OCR** that should be applied to existing documents.
- **A user-facing "download original" feature.**
