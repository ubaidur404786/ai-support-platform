# The model service image: the answer model (Qwen2.5-1.5B, ~1.1 GB) and the
# HTTP server around it. The model is downloaded while the image is built,
# so the image is big (~1.5 GB) but runs anywhere without extra steps: the
# same image works in Docker Compose, Kubernetes and on AWS.
#
# Build from the repository root:
#   docker build -f docker/model-service/Dockerfile -t support-model .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GENERATION_MODEL_PATH=/app/models/generation/qwen2.5-1.5b-instruct-q4_k_m.gguf

WORKDIR /app

# The prebuilt llama.cpp wheels need the OpenMP runtime (libgomp1), which the
# slim image leaves out.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Download the model FIRST, in its own layer. Docker reuses a layer until
# something before it changes, so with the download at the top, a code or
# library change rebuilds in seconds instead of downloading 1.1 GB again.
# (It was after the pip install at first, and adding one library to
# requirements.txt meant downloading the model again.)
# HF_HUB_DISABLE_XET: use a plain HTTP download - the newer transfer
# protocol failed half-way once during a build here. The loop retries twice.
COPY scripts/download_generation_model.py scripts/
RUN pip install --no-cache-dir huggingface_hub==1.33.0 \
    && for attempt in 1 2 3; do \
         HF_HUB_DISABLE_XET=1 python scripts/download_generation_model.py && break; \
         echo "download failed, retrying"; sleep 10; \
       done \
    && test -f "$GENERATION_MODEL_PATH"

COPY requirements.txt constraints.txt
COPY docker/model-service/requirements.txt requirements.txt
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt

COPY app/__init__.py app/
COPY app/core/__init__.py app/core/config.py app/core/logging.py app/core/
COPY app/model_service app/model_service

# The files stay owned by root and are only readable by this user, which is all
# the service needs. (A "chown -R" here would copy every file into a new layer:
# it made the model image 1.1 GB bigger.)
RUN useradd --create-home app
USER app

EXPOSE 8001

# One worker process only: each one would load its own copy of the model (v12).
CMD ["uvicorn", "app.model_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
