# v10: RAG (answers written from the knowledge base)

Search finds passages. v10 turns them into an **answer**, and knows when **not** to answer.
`POST /answers` retrieves the closest chunks (v8/v9) and drops those below a measured relevance
threshold. If any are left, a small local language model (Qwen2.5-1.5B, run on the CPU by
llama.cpp) writes a short answer from them only. The response lists the chunks it used as sources.

![v10 architecture](../../architecture/v10.svg)

## What this version adds

- `POST /answers` → `answer`, `answered` (true/false), `reason`, numbered `sources`.
- Two guards against made-up answers: the **relevance threshold** (0.30; the model is not called
  below it) and the model's **"I don't know"** instruction.
- Its own rate limit: 10 answers per minute per user.
- `evaluation/rag_questions.jsonl`: 30 answerable and 20 unanswerable questions.
- `scripts/choose_relevance_threshold.py` and `scripts/evaluate_rag.py`.

## Run

```bash
pip install -r requirements.txt
```

The model file (~1.1 GB) goes into `models/generation`. Check that the disk has the space:

```bash
python scripts/download_generation_model.py
```

```bash
uvicorn app.main:app
```

```bash
python -m app.worker
```

## Try it

```bash
curl -X POST http://127.0.0.1:8000/answers -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"How do I cancel my subscription?\"}"
```

```bash
curl -X POST http://127.0.0.1:8000/answers -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"What is the capital of France?\"}"
```

The first gives an answer with its source, after a few seconds. The second gives `"answered":
false` at once: nothing in the knowledge base is relevant, so the model is never asked.

## Test

```bash
pytest tests/test_answers_api.py -v
```

Most tests replace the model with a fake that records what it was sent, so they check our code:
which chunks reach the model, and when it is not called at all. One test runs the real model, and is
skipped if it is not downloaded.

## Results (measured locally, CPU laptop, 50 questions)

| | 1.5B (chosen) | 0.5B |
|---|---|---|
| Answerable: correct | 0.70 | 0.70 |
| Unanswerable: refused | **0.95** | 0.80 |
| Unanswerable: hallucinated | **1 of 20** | 4 of 20 |
| Time per answer (P50, API) | 7.4 s | 6.3 s |
| Time when refused by the threshold | 0.12 s | 0.16 s |

## Documents

- Full version document: [v10-rag.md](../v10-rag.md)
- [ADR-027: Generate answers locally with a small model run by llama.cpp](../../adr/ADR-027-local-rag-with-llama-cpp.md)
- [ADR-028: Refuse before generating: a measured relevance threshold](../../adr/ADR-028-relevance-threshold-before-generation.md)
- Previous version: [v9: Vector search](../v9-vector-search.md)
