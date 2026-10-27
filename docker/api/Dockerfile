# The API image. The worker uses the same image with a different command,
# because it is the same code and needs the same libraries.
#
# Build from the repository root:
#   docker build -f docker/api/Dockerfile -t support-api .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first, so Docker can reuse this layer when only code changes.
# The root requirements.txt is used as a constraints file (-c): it fixes the
# exact version of everything, but only what docker/api/requirements.txt
# lists gets installed - no model libraries in this image.
COPY requirements.txt constraints.txt
COPY docker/api/requirements.txt requirements.txt
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt

COPY app ./app
COPY ml ./ml
COPY alembic ./alembic
COPY alembic.ini .

# Train the small ticket classifier inside the image (ADR-003).
RUN python ml/train.py

# Run as a normal user, not root: someone who breaks into the process
# cannot change the files in the image.
# The files stay owned by root and are only readable by this user, which is all
# the service needs. (A "chown -R" here would copy every file into a new layer:
# it made the model image 1.1 GB bigger.)
RUN useradd --create-home app
USER app

EXPOSE 8000

# --proxy-headers: from v17 every browser request arrives through nginx, so
# without it the API would see nginx's address for everyone, and the
# per-address login limit would be one budget for all users. Trusting the
# X-Forwarded-For header is safe ONLY because the API is not reachable from
# outside: the one caller that can set it is our own nginx.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
