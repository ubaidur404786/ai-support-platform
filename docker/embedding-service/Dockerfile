# The embedding service image: the embedding model and the little HTTP
# server around it. The model (~90 MB) is downloaded while the image is
# built, so a container starts without any network access.
#
# Build from the repository root:
#   docker build -f docker/embedding-service/Dockerfile -t support-embedding .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EMBEDDING_CACHE_DIR=/app/models/embeddings

WORKDIR /app

COPY requirements.txt constraints.txt
COPY docker/embedding-service/requirements.txt requirements.txt
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt

# Only the files this service runs. Without the rest of app/, the image
# cannot accidentally depend on the database or the API.
COPY app/__init__.py app/
COPY app/core/__init__.py app/core/config.py app/core/logging.py app/core/
COPY app/embedding_service app/embedding_service

# Download the model now (build time), not when the first container starts.
RUN python -c "from app.core.config import settings; from app.embedding_service import model; model.load(settings.embedding_model)"

# The files stay owned by root and are only readable by this user, which is all
# the service needs. (A "chown -R" here would copy every file into a new layer:
# it made the model image 1.1 GB bigger.)
RUN useradd --create-home app
USER app

EXPOSE 8002

CMD ["uvicorn", "app.embedding_service.main:app", "--host", "0.0.0.0", "--port", "8002"]
