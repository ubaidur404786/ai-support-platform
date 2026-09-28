"""The background worker: turns queued uploads into searchable documents.

Run it next to the API, in its own terminal:

    python -m app.worker

It is a plain loop - no framework. Each turn it rescues documents abandoned by
a crashed worker, then processes the next queued document; when there is none,
it sleeps for WORKER_POLL_SECONDS and asks again. Several workers can run at
once: the database hands each document to exactly one of them.
"""

import logging
import time

# Imported for their side effect: registering the users and organizations tables.
# Documents point at both, and SQLAlchemy must know every table a foreign key
# names before it can write a row. The API imports these through its routers;
# the worker has no routers, and without these lines its first save failed with
# "could not find table 'users'" - found in the v7 measurement, not by the tests,
# because the test process imports everything.
import app.auth.models  # noqa: F401
import app.tickets.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.documents.processing import process_next_document, requeue_stale_documents

logger = logging.getLogger("app.worker")


def run_forever() -> None:
    logger.info("Worker started; checking for documents every %s s", settings.worker_poll_seconds)
    while True:
        try:
            # A fresh session per turn: nothing from one document can leak into
            # the next, and a broken connection is replaced on the next turn.
            with SessionLocal() as session:
                requeue_stale_documents(session)
                found_work = process_next_document(session)
        except Exception:
            # Usually the database is unreachable. The worker must not die
            # because of that: log it, wait, try again. Any document it was
            # holding is rescued later by requeue_stale_documents.
            logger.exception("Worker turn failed; retrying in %s s", settings.worker_poll_seconds)
            found_work = False

        if not found_work:
            time.sleep(settings.worker_poll_seconds)


def main() -> None:
    configure_logging(settings.log_level)
    try:
        run_forever()
    except KeyboardInterrupt:
        # Ctrl+C. A document being processed at this moment stays PROCESSING
        # and is put back in the queue after WORKER_STALE_AFTER_SECONDS.
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
