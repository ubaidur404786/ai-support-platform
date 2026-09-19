# Small official Python image; "slim" leaves out build tools we don't need.
FROM python:3.12-slim

# Send logs straight to the terminal instead of buffering them.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies before copying code: Docker caches this layer and only
# re-runs it when requirements.txt changes, so code edits rebuild quickly.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY ml ./ml

# Train inside the image so the model is built with the exact library versions
# that will serve it. v0 simplification: model and code ship as one unit.
RUN python ml/train.py

EXPOSE 8000

# 0.0.0.0 = listen on all interfaces, required so traffic from outside the
# container can reach the server (127.0.0.1 would only be visible inside it).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]