# v11: Answer streaming

An answer used to arrive all at once after ~6–7 s of blank screen. `POST /answers/stream` sends the
same answer while it is being written, as NDJSON (one JSON object per line). The sources come
first, in ~0.15 s, then the text, then a final `done` line that says whether it counts as an answer.

This version also records a **measured "no"**. Hybrid retrieval, the planned v11, lowered hit@3 from
0.93 to 0.83 on our questions, so it was not built into the product.

![v11 architecture](../../architecture/v11.svg)

## What this version adds

- `POST /answers/stream` (`application/x-ndjson`): `sources` → `text` … → `done` (or `error`).
- A refusal is never streamed as text. The first 40 characters are checked, and generation stops if
  the model says it does not know.
- Every error with a status code (422, 429, 503) is raised before the first line.
- `scripts/measure_answer_streaming.py` and `scripts/compare_hybrid_retrieval.py`.

## Run

As in v10 (API, worker, and the model downloaded with `python scripts/download_generation_model.py`).

## Try it

```bash
curl -N -X POST http://127.0.0.1:8000/answers/stream -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"How do I cancel my subscription?\"}"
```

Expected output, one line at a time:

```
{"type": "sources", "sources": [{"number": 1, "document_title": "Cancelling your subscription", ...}]}
{"type": "text", "text": "Workspace owners can cancel from Settings > Billing > "}
{"type": "text", "text": "Cancel subscription"}
...
{"type": "done", "answered": true, "reason": "answered", "answer": "Workspace owners can cancel ..."}
```

## Test

```bash
pytest tests/test_answers_api.py -v
```

## Results (measured locally, CPU laptop, 25 answered questions)

| | P50 |
|---|---|
| Whole answer (`/answers`) | 6.15 s |
| Stream: sources | **0.15 s** |
| Stream: first text | 5.50 s |
| Stream: done | 7.22 s |

The first text is not much earlier, because the model spends most of its time **reading the
prompt**: ~400 tokens took 7.35 s to the first token, and ~170 tokens took 2.81 s.

## Documents

- Full version document: [v11-answer-streaming.md](../v11-answer-streaming.md)
- [ADR-029: Do not add hybrid retrieval, a reranker or a new embedding model yet](../../adr/ADR-029-no-hybrid-retrieval-yet.md)
- [ADR-030: Stream answers as NDJSON](../../adr/ADR-030-stream-answers-as-ndjson.md)
- Previous version: [v10: RAG](../v10-rag.md)
