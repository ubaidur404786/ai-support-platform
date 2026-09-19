# v0 — Baseline

The smallest working version of the platform: one FastAPI process that classifies a support
ticket into `billing`, `technical_issue`, `account_access` or `feature_request` using a
TF-IDF + Logistic Regression model loaded at startup.

![v0 architecture](../../architecture/v0.svg)

## What this version contains

- `POST /classify` — text in, `{label, confidence, model_version}` out
- `GET /health` — reports whether the model is loaded
- `ml/train.py` — trains and evaluates the model, writes `models/ticket_classifier.joblib`
- 200-row labelled dataset in `ml/data/tickets.csv`
- 16 tests including failure cases (invalid input, missing model, model crash)
- Latency measurement script and a Dockerfile

## Run

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python ml/train.py
uvicorn app.main:app
```

Interactive docs: http://127.0.0.1:8000/docs

Or with Docker:

```bash
docker build -t ai-support-platform:v0 .
docker run --rm -p 8000:8000 ai-support-platform:v0
```

## Test

```bash
pytest -v
```

## Measure

```bash
python scripts/measure_latency.py --requests 1000 --concurrency 10
```

## Results (measured locally)

- Classifier: accuracy 0.90, macro F1 0.90 on a 50-example held-out split
- Inference: 1.7 ms per prediction; ~12 ms per HTTP request; ~80 req/s ceiling
- Under 10 concurrent clients, P50 rises to 134 ms — requests queue because inference is serial

## Documents

- Full version document: [v0-baseline.md](../v0-baseline.md)
- [ADR-001 — Classical ML baseline classifier](../../adr/ADR-001-classical-ml-baseline-classifier.md)
- [ADR-002 — In-process model serving](../../adr/ADR-002-in-process-model-serving.md)
- [ADR-003 — Model trained inside the Docker image](../../adr/ADR-003-model-trained-in-docker-image.md)
