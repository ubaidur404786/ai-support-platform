#!/bin/sh
# Runs every experiment of the lab, one setup after another, and saves
# results/*.json. Takes about 20 minutes. Then: python plot_results.py
set -e
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: keep /api/... as it is

LEVELS=1,10,50,100,200
lt() { docker compose $FILES run --rm loadtest python loadtest/loadtest.py --users $LEVELS --seconds 10 "$@"; }
start() {
    docker compose -f docker-compose.yml -f step2-gateways.yml -f step2-cells.yml \
        -f step3-replicas.yml -f step4-shards.yml down -v --remove-orphans >/dev/null 2>&1
    FILES="$*"
    docker compose $FILES up -d --wait >/dev/null 2>&1
}

echo "== step 1: one gateway"
start -f docker-compose.yml
lt --path /api/users --max-id 1000 --label step1-users
lt --path /api/tickets --label step1-tickets
lt --path /api/tickets/stats --no-id --label step1-stats
lt --path /api/tickets --method POST --label step1-writes

echo "== step 2a: three gateways, one copy of each service"
start -f docker-compose.yml -f step2-gateways.yml
lt --path /api/users --max-id 1000 --label step2a-users
lt --path /api/tickets --label step2a-tickets

echo "== step 2b: three gateways, each with its own services"
start -f docker-compose.yml -f step2-gateways.yml -f step2-cells.yml
lt --path /api/users --max-id 1000 --label step2b-users
lt --path /api/tickets --label step2b-tickets

echo "== step 3: two read replicas"
start -f docker-compose.yml -f step3-replicas.yml
lt --path /api/tickets/stats --no-id --label step3-stats
lt --path /api/tickets --method POST --users 50 --label step3-writes-under-replication &
sleep 5
docker compose $FILES exec -T db psql -U lab -d lab -c \
  "SELECT application_name, client_addr, state, write_lag, replay_lag FROM pg_stat_replication;" | tee results/step3-replication-lag.txt
wait

echo "== step 4: two shards"
start -f docker-compose.yml -f step4-shards.yml
lt --path /api/tickets --method POST --label step4-writes
lt --path /api/tickets/stats --no-id --label step4-stats
for db in db db-shard-1; do
  docker compose $FILES exec -T $db psql -U lab -d lab -tAc "SELECT '$db', count(*) FROM tickets;"
done | tee results/step4-rows-per-shard.txt

docker compose $FILES down -v >/dev/null 2>&1
echo "done"
