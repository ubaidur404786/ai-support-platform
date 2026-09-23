# ADR-006 — Flag low-confidence predictions for human review

Status: accepted (v1)

## Context

`POST /tickets` routes a ticket by asking the classifier for a label. The classifier is a
four-way Logistic Regression: it always returns one of `billing`, `technical_issue`,
`account_access`, `feature_request`, and it has **no "unknown" or "out of scope" class**. It
therefore produces a confident-looking answer for input that is not a support ticket at all.

Measured example: the text `"hello"` was classified as `technical_issue` with confidence 0.31.
Nothing about the response distinguishes that from a correct, certain routing decision.

Acting on such a prediction mis-routes the ticket. Rejecting it loses the ticket. Neither is
acceptable in a support workflow.

## Options

1. **Return the label regardless.** Simplest. A wrong route is indistinguishable from a right
   one, and the failure is silent — the worst property a system can have.
2. **Reject requests below a confidence threshold** (e.g. HTTP 422). The customer's ticket is
   thrown away because the model is weak, which is not the customer's problem.
3. **Store the ticket and mark it for human review below a threshold.** The ticket is kept, the
   model's best guess is kept, and the uncertainty is explicit in the data.
4. **Return a probability distribution over all four labels and let the caller decide.** More
   information, but pushes the decision to every consumer, and each of them then needs the
   same threshold logic.
5. **Calibrate the model and use a principled threshold.** Correct in the long run; requires
   calibration work and a held-out set larger than this project currently has.

## Decision

Option 3. `TicketService.submit` compares the prediction's confidence against
`LOW_CONFIDENCE_THRESHOLD` (default 0.55, read from configuration) and sets
`needs_review: true` on the stored ticket when it falls below. The ticket is stored either way,
the flag appears in the API response, and `GET /tickets?needs_review=true` returns exactly the
queue a human should work through. Each flagged ticket is also logged with its label,
confidence, and the threshold in force.

## Why?

- It separates two different things that the flat design conflated: what the model *said*, and
  whether the system should *act* on it without a human. Both are recorded.
- It costs nothing — no extra model, no extra service, one comparison per request.
- The threshold is configuration, not a constant, because the right value is an operational
  decision: it depends on the model in use and on how much review capacity the team has.
  `LOW_CONFIDENCE_THRESHOLD=0.7` changes the policy without a code change.
- It creates the first measurable AI-operations signal in the project: the proportion of
  tickets flagged is a number that can be watched over time, and a sudden rise is evidence of
  model drift or of traffic the model was not trained for.
- Logging the flagged cases produces exactly the sample worth labelling to improve the model.

## Trade-offs

Gain: no silent mis-routing; a review queue that falls out of the data model; a policy that can
be tuned in production; a drift signal.

Lose:

- **0.55 is a guess.** It was chosen because it sits above the 0.31 observed on junk input and
  below the 0.85 observed on a clear billing ticket. It is not derived from a
  precision/recall trade-off on a validation set.
- **Logistic Regression probabilities are not calibrated**, so "0.55 confidence" does not mean
  "55% likely to be correct". The threshold is a useful ordering, not a probability.
- The flag does not fix the underlying weakness: the model still has no way to say "this is not
  a support ticket".
- Review capacity is assumed to exist. A threshold set too high creates a queue nobody works,
  which is the same as not flagging at all.

## Future Trigger

Revisit when any of these is true:

- An evaluation set exists that is large enough to choose the threshold from a
  precision/recall curve rather than from two observed examples.
- The flag rate is measured and turns out to be too high (humans overwhelmed) or too low
  (mis-routes still reaching customers).
- The classifier is replaced, since a new model changes what a given confidence value means —
  the threshold must be re-derived, not carried over.
- An explicit out-of-scope class or a rejection option is added to the model, which would
  handle non-ticket input directly instead of routing it and flagging it.
- Human review becomes a workflow rather than a flag (assignment, decisions recorded,
  corrections fed back into training data) — that is a feature in its own right, and the
  corrected labels are the most valuable training data the platform can produce.
