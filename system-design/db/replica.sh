#!/bin/sh
# Starts a read replica: copies the primary once, then streams every change.
set -e

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    # pg_basebackup copies the whole primary. -R writes the settings that
    # make this server a replica that follows the primary (PRIMARY_HOST).
    until PGPASSWORD=replicator pg_basebackup -h "$PRIMARY_HOST" -U replicator \
            -D "$PGDATA" -R -X stream; do
        echo "waiting for the primary..."; sleep 2
    done
    chmod 700 "$PGDATA"
fi

exec postgres
