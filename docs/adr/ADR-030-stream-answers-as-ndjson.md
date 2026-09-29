# ADR-030: Stream answers as NDJSON: sources at once, then the text

Status: accepted (v11)

## Context

`POST /answers` (v10) returns nothing until the whole answer exists: ~6–7 s P50 on this CPU. During
that time the user sees a blank screen, although the sources (the articles the answer will come
from) were known after ~0.15 s.

## Options

1. **Keep one response**, and show a spinner.
2. **Stream NDJSON** ("newline-delimited JSON"): one JSON object per line, each sent as soon as it
   exists.
3. **Server-Sent Events (SSE)**: the browser standard for server push (`data: …` lines). The
   browser's built-in `EventSource` only sends GET requests, and our question is a POST body.
4. **WebSockets**: two-way, a connection that stays open. Nothing here needs the client to talk
   back mid-answer.

## Decision

Option 2: a new endpoint, `POST /answers/stream` (`application/x-ndjson`). `POST /answers` stays
unchanged. Lines, in order:

```
{"type": "sources", "sources": [...]}                   once, ~0.15 s (if anything relevant)
{"type": "text", "text": "..."}                         many, as the model writes
{"type": "done", "answered": ..., "reason": ..., "answer": "..."}   always last
{"type": "error", "detail": "..."}                      instead of "done", if generation crashes
```

## Why?

- **NDJSON is the simplest stream.** Any HTTP client can read it line by line, and each line is
  ordinary JSON. SSE would add its own framing, and WebSockets a protocol, for no gain here.
- **Errors that have a status code happen first.** An empty question (422), a missing model (503)
  or a used-up budget (429) is checked, and the model is loaded, **before** the first line. After
  that the response is already `200 OK`, so a crash can only be reported as an `error` line.
- **A refusal must never appear as text.** The first 40 characters are held back and checked for
  "I don't know" and its variants. If it is a refusal, generation **stops** there, which saves the
  rest of the CPU time, and only `done` with `answered: false` is sent. A refusal that comes later
  in the reply is corrected by the `done` line, which always has the last word.

Measured (25 answered questions, two separate passes, through the API, 1.5B model):

| | P50 |
|---|---|
| `POST /answers`, whole answer | 6.15 s |
| stream: sources line | **0.15 s** |
| stream: first text | 5.50 s (max 9.13 s) |
| stream: done | 7.22 s |
| streamed answer identical to the non-streamed one | 25 of 25 |

**The text starts only ~0.6 s earlier**, and the reason was measured in-process. On this CPU the
model spends most of an answer **reading the prompt** (prompt processing, or "prefill"), not writing
the reply:

| Prompt | First token, P50 | Whole reply, P50 |
|---|---|---|
| 3 sources, ~396 tokens | 7.35 s | 9.11 s |
| 1 source, ~173 tokens | 2.81 s | 4.64 s |

Setting llama.cpp to 4, 6 (the default) or 8 threads changed nothing measurable (7.20–7.35 s).
Streaming cannot hide prefill. What it does fix is the empty wait before the **sources**, from ~6 s
to 0.15 s.

## Trade-offs

Gain: the sources are on screen almost at once, and the answer appears as it is written. The
answer text is the same (temperature 0, 25 of 25 identical). A refusal stops generation early.

Lose:

- Two endpoints to keep consistent. They share `retrieve()`, the prompt and the refusal check.
- The client must read the `done` line and trust it over the text it already showed.
- Streaming was ~1 s slower in total (7.22 s vs 6.15 s P50): 40 characters are held back, and each
  piece passes through the thread pool.
- The model lock is held while the response is being sent. A slow client keeps the next question
  waiting.

## Future Trigger

- **Time to first text matters more than completeness**: shorten the prompt (fewer or shorter
  chunks). One source cut the first token from 7.35 s to 2.81 s, but lowered correctness in
  `evaluate_rag.py` (0.70 → 0.67, sources right 0.80 → 0.73).
- **A browser client with `EventSource`**: add an SSE variant.
- **Prefill on a CPU is the bottleneck.** A GPU, or a separate model service on better hardware,
  is the real fix (v14/v15).
