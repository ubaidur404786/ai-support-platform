# ADR-001 — Classical ML model (TF-IDF + Logistic Regression) as the baseline classifier

Status: accepted (v0)

## Context

The first capability of the platform is routing a support ticket to one of four categories.
The project runs without a paid API budget or cloud GPU, and the baseline must be simple enough
to measure and reason about before any infrastructure is added.

## Options

1. **Zero-shot transformer** (e.g. an NLI model from Hugging Face). No training data needed and
   better language understanding. Costs: 300 MB–1.6 GB download, PyTorch as a dependency,
   hundreds of milliseconds per request on CPU.
2. **Small local LLM** (e.g. via Ollama). Flexible, but several GB, slow on CPU, and its free-text
   output is hard to evaluate reliably for a fixed-category task.
3. **TF-IDF + Logistic Regression** (scikit-learn). Requires labelled examples. Model is
   kilobytes, predicts in ~2 ms on CPU, evaluates with standard classification metrics.
4. **Fine-tuned small transformer** (e.g. DistilBERT). Better accuracy potential, but needs
   PyTorch, a training loop, and more data than exists at this stage.

## Decision

Option 3. A scikit-learn `Pipeline` of `TfidfVectorizer(ngram_range=(1, 3))` and
`LogisticRegression(C=10.0)`, trained on a 200-row hand-written dataset, saved with joblib
together with its metadata (model version, labels, training timestamp, scikit-learn version).

## Why?

- The task is a fixed four-way classification; a linear model on word features is a proven fit.
- It runs anywhere, with no downloads, GPU, or external service — the free/local-first constraint.
- It gives probabilities, so the API can report a confidence value.
- It makes the whole model lifecycle real at zero cost: data → training → artifact → serving →
  evaluation. Later versions change the pieces, not the shape.
- Its weaknesses are measurable, which provides honest triggers for a better model later.

## Trade-offs

Gain: tiny artifact, ~2 ms inference, simple evaluation, no infrastructure, deterministic training.

Lose: the model only knows vocabulary seen in training and does not generalize to new phrasing,
typos, or long multi-topic messages. Confidence values from Logistic Regression are not
calibrated probabilities. A hand-written dataset makes the measured 0.90 accuracy optimistic.

## Future Trigger

Revisit when any of these happens:

- A realistic dataset (real or public support tickets) shows accuracy well below what the
  business needs.
- The category set grows or becomes hierarchical.
- Inputs become long documents rather than one-sentence tickets.
- The platform gains embedding infrastructure (for retrieval) that a transformer classifier
  could share.

Expected next step at that point: a fine-tuned small transformer with a proper training
pipeline and experiment tracking (planned as v8).
