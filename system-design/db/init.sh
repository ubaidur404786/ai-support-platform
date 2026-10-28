#!/bin/sh
# Runs once, the first time a database container starts with an empty disk.
set -e

# 1. Tables and data (SHARD_* come from docker-compose; default = no sharding).
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -v shard_count="${SHARD_COUNT:-1}" -v shard_index="${SHARD_INDEX:-0}" \
     -f /sql/schema.sql

# 2. A user that replicas log in as to copy the data (step 3).
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -c "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator';"
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
