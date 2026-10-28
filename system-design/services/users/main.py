"""Users service: owns the users table. Only gateways call it.

Its work is almost all waiting for the database (I/O), so one process
can serve many requests at the same time.
"""

import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool

# The container's name in Docker; shows WHICH copy answered.
HOST = socket.gethostname()

# A pool keeps a few database connections open, so a request does not
# pay ~5 ms to open a new one each time.
pool = AsyncConnectionPool(os.environ["DATABASE_URL"], min_size=2, max_size=10, open=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "served_by": HOST}


@app.get("/users/{user_id}")
async def get_user(user_id: int):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id,))
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {"id": row[0], "name": row[1], "email": row[2], "served_by": HOST}
