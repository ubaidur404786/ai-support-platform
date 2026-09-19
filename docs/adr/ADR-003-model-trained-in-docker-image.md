# ADR-003 — Train the model inside the Docker image build

Status: accepted (v0)

## Context

The Docker image must contain a model to serve. The model artifact is a joblib pickle, which
is tied to the exact scikit-learn version that produced it. During development the model was
accidentally trained in one environment (scikit-learn 1.4.2) and loaded in another (1.9.1),
producing `InconsistentVersionWarning` on every load. Across a larger version gap this fails
outright or silently changes predictions.

## Options

1. **`COPY` the artifact from the developer's disk** into the image. Fast builds, but the image
   depends on whatever file happens to be present locally, trained with whatever library
   version that machine had.
2. **Train during `docker build`** (`RUN python ml/train.py`) after installing pinned
   dependencies. The model is always produced by the same library versions that serve it.
3. **Download the artifact from a model registry or object storage** at build or start time.
   Correct long-term design, but requires infrastructure that does not exist yet.

## Decision

Option 2. The Dockerfile installs `requirements.txt`, copies `app/` and `ml/`, runs
`python ml/train.py`, and serves with uvicorn. `models/` is listed in `.dockerignore` so a
stale local artifact can never leak into the image. The artifact also records
`sklearn_version` in its metadata so a mismatch is visible instead of a surprise.

## Why?

- It removes an entire class of bug (artifact/runtime version mismatch) with one line.
- Training takes seconds on a 200-row dataset, so build time is not a concern.
- No new infrastructure is required.
- The image is self-contained and reproducible: same `requirements.txt` + same data + same
  seed → same model.

## Trade-offs

Gain: model and runtime are guaranteed consistent; reproducible image; no external dependency
at build time.

Lose:

- Model and code are coupled: shipping a new model means rebuilding the image, and shipping a
  code fix retrains the model.
- Training data must be in the build context, which stops being practical once the dataset is
  large or private.
- Build time grows with training time; this is only acceptable while training is seconds.
- No record of which model is in which image beyond the hard-coded `MODEL_VERSION` string.

## Future Trigger

Revisit when:

- Training takes more than a minute or needs a GPU.
- Models are updated more often than code, or need to be rolled back independently of code.
- More than one model version must be compared side by side (A/B, canary).
- Training data can no longer live in the repository.

Expected next step: a model registry (e.g. MLflow) with artifacts stored outside the image and
pulled by version at start time, together with a rollout strategy (planned as v20).
