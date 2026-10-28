"""Tickets service: owns the tickets table and classifies tickets.

Unlike the users service, it does CPU work for every request (a stand-in
for a small AI model). A Python process runs CPU work on one core at a
time, so this service is the one that slows down first under load.
"""

import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool

HOST = socket.gethostname()

# How much CPU each classification costs. Fixed work (not a fixed time),
# so when the CPU is busy the request really takes longer.
WORK_UNITS = int(os.environ.get("WORK_UNITS", "200000"))

pool = AsyncConnectionPool(os.environ["DATABASE_URL"], min_size=2, max_size=10, open=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(lifespan=lifespan)


def classify(text: str) -> str:
    """Pretend model: burn a fixed amount of CPU, then apply a keyword rule."""
    total = 0
    for i in range(WORK_UNITS):
        total += i * i
    return "urgent" if "refund" in text or "down" in text else "normal"


@app.get("/health")
async def health():
    return {"status": "ok", "served_by": HOST}


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id, user_id, text FROM tickets WHERE id = %s", (ticket_id,))
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    # Runs on the event loop on purpose: while it runs, this process
    # answers nothing else. That is the real cost of CPU work in Python.
    category = classify(row[2])
    return {"id": row[0], "user_id": row[1], "category": category, "served_by": HOST}
