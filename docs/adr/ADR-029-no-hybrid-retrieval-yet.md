# ADR-029: Do not add hybrid retrieval, a reranker or a new embedding model yet

Status: accepted (v11). This decision is **not** to build something, and the measurement below is
the reason.

## Context

After v10, the right article was among an answer's sources for 80% of answerable questions. The
plan named hybrid retrieval as v11: combine keyword search (PostgreSQL full text) with semantic
search, because keyword search is strong on exact wording. Before building it, the idea was tested
on the same questions that measure the current system.

The 6 answerable questions whose sources missed the right article in v10 are all **paraphrases**:

| Question | Expected article | What the sources held |
|---|---|---|
| "I want you to forget everything you know about me" | privacy requests | nothing (score 0.19, below the threshold) |
| "When can I talk to a human?" | contact support | nothing (score 0.25) |
| "The six digit number from my phone app is not accepted" | two-factor auth | mobile sync, reset password, contact |
| "Can a colleague get access to our account?" | invite teammates | roles, privacy |
| "Can I close our company account for good?" | delete workspace | reset password, cancel, privacy |
| "Your website is not loading, is it down for everyone?" | service status | supported browsers |

None of them shares a distinctive word with its article, so keyword search has nothing to add.

## Options (all measured)

1. **Hybrid retrieval, reciprocal rank fusion (RRF)**: add up `1 / (60 + rank)` from each list. It
   was tried with keyword search matching any query word and matching all words, and with the keyword
   list weighted 1, 0.5 and 0.25.
2. **A cross-encoder reranker** (`ms-marco-MiniLM-L-6-v2`, 80 MB, through fastembed). It re-scores
   the top 20 chunks by reading the question and chunk together.
3. **A stronger embedding model** (`bge-small-en-v1.5`, the runner-up in ADR-023).
4. **Keep semantic search as it is.**

## Decision

Option 4. `scripts/compare_hybrid_retrieval.py` stays in the repository so the comparison can be
re-run when the data changes.

## Why?

The 30 retrieval questions, articles ranked by their best chunk:

| Method | Evaluation KB (20 chunks): hit@1 / hit@3 / MRR | Same KB + 12,000 unrelated paragraphs: hit@1 / hit@3 / MRR |
|---|---|---|
| **semantic (current)** | **0.80 / 0.93 / 0.87** | 0.67 / 0.83 / 0.77 |
| hybrid, any word | 0.73 / 0.83 / 0.82 | 0.53 / 0.83 / 0.70 |
| hybrid, any word, keyword weight 0.5 | 0.73 / 0.83 / 0.82 | 0.57 / 0.87 / 0.72 |
| hybrid, all words | 0.80 / 0.93 / 0.87 | 0.70 / 0.83 / 0.79 |
| semantic top 20 + reranker | 0.77 / 0.83 / 0.83 | not run |

- **Hybrid with "any word" is worse.** Paraphrase hit@3 fell from 0.87 to 0.67: common words
  ("account", "get", "my") pull the wrong articles up. With RRF, being in the keyword list at all
  is worth more than several places in the semantic list.
- **Hybrid with "all words" ties.** The keyword list is usually empty for a full question, so the
  result is semantic search again. It gained one question on the large corpus (hit@1 0.67 → 0.70),
  which is 3 points: one question, within the noise.
- **The reranker is worse** here (hit@3 0.83), and it took ~4.8 s to score 20 chunks on this CPU.
  That would put it in front of a 7 s answer.
- **bge-small ranks slightly better** (hit@1 0.87, MRR 0.90) but **refuses worse**. Its scores for
  answerable and unanswerable questions overlap more, and the best threshold on one half gave
  0.80 answered / 0.80 refused on the other, against 0.93 / 0.90 now. Switching would also mean
  re-embedding every chunk and re-measuring the threshold.

The reranker and bge-small runs were one-off experiments, and their scripts are not in the
repository. The hybrid comparison is.

## Trade-offs

Gain: no extra query, index, model or tuning parameter. Retrieval stays one SQL statement.

Lose: exact identifiers (error codes, invoice numbers, product SKUs) are still found only by
meaning, and embeddings are weak at them. **The evaluation set contains none**, so this weakness is
unmeasured, not absent.

## Future Trigger

- **The knowledge base gains identifier-heavy content**, or real users search for codes: add such
  questions to the evaluation first, then re-run `compare_hybrid_retrieval.py`. Hybrid with
  "all words" is the variant to start from.
- **A larger, harder evaluation set** (multi-chunk documents, near-duplicate articles), where
  ranking matters more than in 20 one-chunk articles: re-test the reranker.
- **The paraphrase misses matter in practice**: re-test stronger embedding models, and re-measure
  the refusal threshold with each one.
