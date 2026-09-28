# ADR-028: Refuse before generating: a measured relevance threshold

Status: accepted (v10)

## Context

Semantic search always returns its closest chunks, even for "What is the capital of France?"
(v8). In search that is harmless, because a person sees a weak result and ignores it. In RAG those
chunks become the "facts" of a generated answer. A model handed an unrelated chunk will often write
a confident answer anyway. That is **hallucination**, and for a support tool it is the worst
failure: a wrong answer the customer believes.

## Options

1. **Let the model decide**: always call it, and tell it to say "I don't know".
2. **A fixed similarity threshold**: if no chunk scores above it, answer "not found" without calling
   the model.
3. **Both**: the threshold removes what is clearly unrelated, and the instruction handles chunks that
   are related but do not contain the answer.
4. **A trained relevance classifier or a reranker** (v12): better, but it is another model to
   evaluate.

## Decision

Option 3. `ANSWER_RELEVANCE_THRESHOLD = 0.30` (cosine similarity), with only chunks above it
sent to the model, at most 3. Then an instruction to reply "I don't know", with that reply (and
three equivalent phrasings the model used) turned into `answered: false`.

## Why?

The threshold was chosen from data (`scripts/choose_relevance_threshold.py`). There are 50 questions,
30 answerable and 20 not (16 close to the product, 4 off-topic), split into halves: the threshold
was **picked on one half and checked on the other**:

| Threshold | tune: answered | tune: refused | test: answered | test: refused |
|---|---|---|---|---|
| 0.25 | 0.93 | 0.50 | 1.00 | 0.70 |
| **0.30** | **0.93** | **0.70** | **0.93** | **0.90** |
| 0.35 | 0.73 | 0.90 | 0.93 | 0.90 |
| 0.40 | 0.67 | 0.90 | 0.67 | 1.00 |

The best scores of the two groups overlap between 0.19 and 0.40. Some answerable paraphrases score
only 0.19 ("I want you to forget everything you know about me"), while one unanswerable question
scores 0.64 ("How many API keys can I create?", which is next to the API keys article). **No
threshold can separate them alone**, so the second guard exists.

Measured end to end with the 1.5B model: the threshold refused 16 of 20 unanswerable questions in
~0.12 s without calling the model, the model declined 3 more, and 1 was answered wrongly
("600 API keys"). Refusing early also saves ~7 s of CPU per refused question.

## Trade-offs

Gain: most unanswerable questions are refused cheaply and deterministically, and the model never
sees unrelated text.

Lose:

- **Two answerable questions are refused** at 0.30 (scores 0.19 and 0.25). This is the price of
  the threshold, paid on paraphrases with unusual wording.
- **The threshold belongs to the embedding model.** A different model produces different scores, so
  the measurement has to be run again.
- **Detecting "I don't know" is string matching.** A refusal phrased some other way counts as an
  answer. Four phrasings were seen in the evaluation. There will be more.
- **50 questions**, written together with the articles. One question moves a rate by 3–5 points.

## Future Trigger

- **Real questions from users** (logged, labelled): re-measure the threshold on them.
- **The overlap zone matters in practice** (too many refusals, or wrong answers near the
  threshold): a reranker (v12) scores question and chunk together, and separates them far better
  than a single similarity number.
