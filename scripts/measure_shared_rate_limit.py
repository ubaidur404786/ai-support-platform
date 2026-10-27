"""How many login attempts get through when the API runs as several copies?

The login limit is AUTH_RATE_LIMIT_PER_MINUTE (10) per client address. This
sends 30 wrong-password logins as fast as it can and counts how many were
let through (401) and how many were refused (429). Used in v16 against
Kubernetes, once with the counters in each API copy's memory and once in Redis:

    python scripts/measure_shared_rate_limit.py http://localhost:30080/api
"""

import sys
import time
from collections import Counter

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:30080/api").rstrip("/")
ATTEMPTS = 30


def main() -> None:
    statuses = Counter()
    start = time.perf_counter()
    with httpx.Client(base_url=BASE, timeout=30) as client:
        for _ in range(ATTEMPTS):
            response = client.post(
                "/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-123"}
            )
            statuses[response.status_code] += 1
    seconds = time.perf_counter() - start
    print(f"{ATTEMPTS} attempts in {seconds:.1f} s: {dict(statuses)}")
    print(f"let through (not 429): {ATTEMPTS - statuses[429]}")


if __name__ == "__main__":
    main()
