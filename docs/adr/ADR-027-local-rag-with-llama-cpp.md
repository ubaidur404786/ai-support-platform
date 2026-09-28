# ADR-027: Generate answers locally with a small model run by llama.cpp

Status: accepted (v10)

## Context

Since v8, semantic search finds the right article (hit@3 0.93), but it returns **chunks, not
answers**. A support agent still has to read three passages to answer "Can I cancel my plan?". The
next step is retrieval-augmented generation (RAG): give a language model the retrieved chunks and
ask it to write a short answer from them only.

Constraints: no paid API, a laptop CPU with ~8 GB of RAM (often under 1 GB free), and no GPU in
use.

## Options

Models:

1. **Qwen2.5-0.5B-Instruct**, 4-bit GGUF (~490 MB).
2. **Qwen2.5-1.5B-Instruct**, 4-bit GGUF (~1.1 GB).
3. A 3B–8B model (Llama 3.x, Qwen2.5-7B): 2–5 GB at 4 bits, which would not fit next to everything
   else on this machine.
4. A paid API (GPT-4-class): ruled out, because the project uses no paid services.

Runtimes:

1. **llama.cpp via `llama-cpp-python`**: a C++ engine built for quantised models on a CPU. Prebuilt
   CPU wheels exist, and the model loads in-process from one file.
2. **Ollama**: a separate server around llama.cpp. It is easy to use, but it is a second service to
   install and run.
3. **transformers + PyTorch**: this is the stack v8 removed (ADR-023) because of its import time and
   size.

## Decision

Qwen2.5-1.5B-Instruct (Apache-2.0), 4-bit, run in the API process by `llama-cpp-python`, with
`temperature=0`. Endpoint: `POST /answers`.

## Why?

Both models, the same prompt, retrieval and threshold, the same 50 questions, through the API
(`scripts/evaluate_rag.py`):

| | 0.5B | **1.5B** |
|---|---|---|
| Answerable (30): answer contains the expected fact | 0.70 | **0.70** |
| Answerable: answered at all | 0.90 | 0.83 |
| Unanswerable (20): refused | 0.80 | **0.95** |
| Unanswerable: **answered anyway** (hallucinated) | 4 of 20 | **1 of 20** |
| Time per answer when the model runs, P50 (API / in-process) | 6.3 s / 3.9 s | 7.4 s / 6.7 s |
| File size | 491 MB | 1,117 MB |

(`evaluation/results/v10-rag.json` and `v10-rag-qwen2.5-0.5b.json`.)

The two models are equally right when the answer exists. The difference is what they do when it
does not. The 0.5B model **invented** answers: "The price of the Team plan is $50 per month", or
"click on the Merge button". A made-up price in a support answer is worse than a slower answer, so
the larger model wins, and the 0.5B model is kept as a one-line setting for machines that cannot
hold 1.1 GB.

llama.cpp, because it is one pip package with a prebuilt wheel and one model file, runs in the
process we already have, and adds no service. A separate model server is the v14/v15 step, once
there is a measured reason to scale it apart from the API.

`temperature=0` makes the output repeatable, so the same question gives the same answer. That
makes the evaluation reproducible, and makes a bad answer something that can be reported and
fixed.

## Trade-offs

Gain: answers instead of passages, locally and for free. Every answer comes with its sources.

Lose:

- **Seconds per answer, and one at a time.** ~7 s P50 on this CPU. One `Llama` object must not be
  used by two threads, so a lock makes concurrent questions wait in line. Measured: one answer
  alone took 6.7 s; with two sent at once, one took 6.5 s and the other **12.4 s**. Other
  endpoints are not slowed, because llama.cpp's C++ code releases Python's GIL: `/health` stayed at a
  P50 of 21 ms during generation (19 ms idle), unlike v6's text extraction.
- **~1.1 GB of RAM in the API process**, on top of the embedding model.
- **Small-model mistakes**: "No." as the answer to "Can I close our account for good?", which is
  wrong. The evaluation measures this, but nothing prevents it.
- **A C++ dependency**: prebuilt wheels are needed, and otherwise pip compiles llama.cpp.

## Future Trigger

- **More than one answer at a time is needed**, or answers slow down other endpoints: move
  generation to its own process or service (v14 model service / v15 LLM serving), with a queue in
  front of it.
- **Correctness below what users accept**: a bigger model on better hardware, or reranking
  retrieved chunks first (v12). The evaluation set decides.
- **A GPU becomes available**: llama.cpp's GPU build uses the same model file and code.
