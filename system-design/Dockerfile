# One small image for every Python part of the lab: gateway, services, load test.
# docker-compose.yml picks which one a container runs with its `command`.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway/ gateway/
COPY services/ services/
COPY loadtest/ loadtest/
