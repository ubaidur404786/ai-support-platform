"""Check the running system from the outside, the way a user would use it.

Works against Docker Compose, Kubernetes or AWS: it only needs the address
of the API.

    python scripts/smoke_test.py http://localhost:8000          # the API itself
    python scripts/smoke_test.py http://localhost:8080/api      # through the web app (v17)
    python scripts/smoke_test.py http://localhost:30080/api     # Kubernetes (v16)

It registers a user, uploads a help article, waits for the worker to make it
searchable, asks a question and reads the streamed answer. Each step prints
how long it took.
"""

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/api").rstrip("/")
ARTICLE = Path("evaluation/knowledge_base/refund-policy.md")


def step(name: str, start: float) -> None:
    print(f"  {name:<42} {time.perf_counter() - start:6.2f} s")


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=180)
    print(f"Smoke test against {BASE}")

    start = time.perf_counter()
    health = client.get("/health").json()
    step("GET /health", start)
    print(f"    {health}")

    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    password = "smoke-test-password"
    start = time.perf_counter()
    client.post(
        "/auth/register", json={"organization_name": f"Smoke {email}", "email": email, "password": password}
    ).raise_for_status()
    token = client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    step("register + log in", start)

    start = time.perf_counter()
    document = client.post(
        "/documents", files={"file": (ARTICLE.name, ARTICLE.read_bytes(), "text/markdown")}
    ).json()
    while (status := client.get(f"/documents/{document['id']}").json()["status"]) not in ("ready", "failed"):
        time.sleep(0.5)
    step(f"upload -> {status}", start)

    start = time.perf_counter()
    first_text = None
    answer = ""
    with client.stream("POST", "/answers/stream", json={"question": "Can I get my money back?"}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if event["type"] == "text":
                if first_text is None:
                    first_text = time.perf_counter() - start
                answer += event["text"]
            if event["type"] == "done":
                answer = event["answer"]
    step("streamed answer", start)
    print(f"    first words after {first_text:.2f} s" if first_text else "    (no text)")
    print(f"    answer: {answer}")


if __name__ == "__main__":
    main()
