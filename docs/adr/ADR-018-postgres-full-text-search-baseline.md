# ADR-018 — Chunks in PostgreSQL, with full-text search as the retrieval baseline

Status: accepted (v6)

## Context

v6 introduces knowledge-base documents. Later versions will embed them (v8), search them by
meaning (v9) and generate answers from them (v10). v6 has to decide where the text lives, what
unit it is stored in, and how it is found *now*, before any of that exists.

A second question matters more than it looks: **how will we know embeddings help?** Without a
measured baseline, "we added vector search" is a statement of fashion rather than an improvement.

## Options

Storage unit:

1. Whole documents only.
2. **Chunks** (~800 characters, overlapping) plus the full extracted text on the document.

Search:

A. `LIKE '%word%'` — no index, no ranking, no stemming.
B. **PostgreSQL full-text search** — a generated `tsvector` column, a GIN index, `ts_rank`.
C. Elasticsearch / OpenSearch — a dedicated search service.
D. Embeddings + a vector index straight away.

Ranking function (within B): `ts_rank` (term frequency) or `ts_rank_cd` (cover density — also
rewards query words appearing close together).

## Decision

Chunks (2), searched with PostgreSQL full-text search (B), ranked with **`ts_rank`**, with
`any`-word matching by default. A 30-question evaluation set measures it.

## Why?

- **Chunks are the unit of retrieval from here on.** Embeddings are computed per passage, and
  answers cite passages. Designing storage around the file would force a redesign in v8.
- **Full-text search is already in the database we run.** Stemming ("refunded" → "refund"), stop
  words, ranking and an inverted index cost one generated column and one index. The column is
  computed by PostgreSQL, so it cannot drift from the text.
- **Not Elasticsearch:** a second service, a second copy of every document, and a sync process
  between them, with no measured need. PostgreSQL search at 39,588 chunks answers in ~108 ms
  under a worst-case vocabulary.
- **Not embeddings yet:** without a keyword baseline, their improvement can't be measured. The
  baseline is now recorded: hit@3 **1.00** on lexical questions and **0.47** on paraphrases.
- **`ts_rank` over `ts_rank_cd`, by measurement.** `ts_rank_cd` was the first choice. Over 39,580
  matching chunks it took **6,368 ms**; `ts_rank` took **64 ms**. Ranking runs for every matching
  row before `LIMIT`, so per-row cost multiplies. Evaluation quality did not drop: hit@3 was 0.73
  with both, and MRR went from 0.64 to 0.66.
- **`any` over `all`:** requiring every word failed 20% of the lexical questions and 100% of the
  paraphrases. Questions rarely share all their words with the answer.

## Trade-offs

Gain: no new service; search is in the same transaction and permission model as the data; a
measured baseline for every later retrieval change.

Lose:

- **No understanding of meaning.** "Money back" does not find "refund". 20% of paraphrased
  questions return nothing.
- **Cost grows with matches.** Ranking touches every matching chunk. When a word appears in 95% of
  chunks, the planner rightly declines the GIN index and scans. That was 84.6 ms at 39,588 chunks.
  Not flat, only bounded.
- **English only.** The `'english'` configuration stems and removes stop words in English. Other
  languages get worse matching.
- **The chunk size is a guess** (800 / 100). Nothing has tuned it yet.

## Future Trigger

- **Paraphrase recall is the measured weakness → embeddings (v8) and vector search (v9).** Compare
  on the same `evaluation/retrieval_questions.jsonl`. Keyword search probably survives as half of a
  hybrid (v11), because it is still perfect on exact terms such as error codes and product names.
- **Search latency grows with corpus size** → cap the number of candidates that get ranked, or
  move to an engine with precomputed ranking (BM25 in a dedicated index, or PostgreSQL's RUM
  extension).
- **Non-English customers** → a language column per document, with the matching text-search
  configuration.
- **A chunk-size experiment** once embeddings exist, since the best size for keyword matching and
  for embeddings may differ.
