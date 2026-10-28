"""Tickets service: owns the tickets table and classifies tickets.

Unlike the users service, it does CPU work for every request (a stand-in
for a small AI model). A Python process runs CPU work on one core at a
time, so this service is the one that slows down first under load.

Database settings (each step of the lab sets different ones):
    DATABASE_URLS       one URL per shard. One URL = no sharding (steps 1-3).
    READ_DATABASE_URLS  read replicas for heavy reads (step 3). Optional.
"""

import itertools
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

HOST = socket.gethostname()

# How much CPU each classification costs. Fixed work (not a fixed time),
# so when the CPU is busy the request really takes longer.
WORK_UNITS = int(os.environ.get("WORK_UNITS", "200000"))


def make_pool(url: str) -> AsyncConnectionPool:
    # A pool keeps a few connections open, so a request does not pay to open one.
    return AsyncConnectionPool(url, min_size=2, max_size=10, open=False)


# Writes and single-ticket reads go to the shard that owns the user.
shards = [make_pool(url) for url in os.environ["DATABASE_URLS"].split(",")]

# Heavy reads go to replicas, one after another (round robin).
# Without replicas, they go to the primary like everything else.
read_urls = os.environ.get("READ_DATABASE_URLS", "")
replicas = [make_pool(url) for url in read_urls.split(",")] if read_urls else []
next_replica = itertools.cycle(replicas)


def shard_for(user_id: int) -> AsyncConnectionPool:
    # The shard key is user_id: all tickets of one user live on one shard.
    return shards[user_id % len(shards)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    for pool in shards + replicas:
        await pool.open()
    yield
    for pool in shards + replicas:
        await pool.close()


app = FastAPI(lifespan=lifespan)


class NewTicket(BaseModel):
    user_id: int
    text: str


def classify(text: str) -> str:
    """Pretend model: burn a fixed amount of CPU, then apply a keyword rule."""
    total = 0
    for i in range(WORK_UNITS):
        total += i * i
    return "urgent" if "refund" in text or "down" in text else "normal"


@app.get("/health")
async def health():
    return {"status": "ok", "served_by": HOST}


@app.get("/tickets/stats")
async def stats():
    """Heavy read: count tickets per category over the whole table."""
    sql = "SELECT count(*), count(*) FILTER (WHERE text LIKE '%refund%') FROM tickets"
    total = refunds = 0
    # With replicas: ask one replica. With shards: ask every shard and add up.
    pools = [next(next_replica)] if replicas else shards
    for pool in pools:
        async with pool.connection() as conn:
            row = await (await conn.execute(sql)).fetchone()
        total, refunds = total + row[0], refunds + row[1]
    return {"tickets": total, "refund_tickets": refunds, "served_by": HOST}


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int, user_id: int | None = None):
    if len(shards) > 1 and user_id is None:
        # Without the shard key we would have to ask every shard.
        raise HTTPException(status_code=400, detail="user_id is required when tickets are sharded")
    pool = shard_for(user_id) if user_id is not None else shards[0]
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id, user_id, text FROM tickets WHERE id = %s", (ticket_id,))
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    # Runs on the event loop on purpose: while it runs, this process
    # answers nothing else. That is the real cost of CPU work in Python.
    category = classify(row[2])
    return {"id": row[0], "user_id": row[1], "category": category, "served_by": HOST}


@app.post("/tickets", status_code=201)
async def create_ticket(ticket: NewTicket):
    """Write: always to the primary of the shard that owns the user."""
    async with shard_for(ticket.user_id).connection() as conn:
        cur = await conn.execute(
            "INSERT INTO tickets (user_id, text) VALUES (%s, %s) RETURNING id",
            (ticket.user_id, ticket.text),
        )
        new_id = (await cur.fetchone())[0]
    return {"id": new_id, "user_id": ticket.user_id, "shard": ticket.user_id % len(shards), "served_by": HOST}
