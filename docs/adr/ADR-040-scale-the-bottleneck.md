# Decision: scale the measured bottleneck; replicate reads later; do not shard

## Context

v19 moves the platform to AWS, where every extra machine costs money. Before that, we need to know which part to copy when users grow. The v18 lab measured this on a model system made of nginx, gateways, services and PostgreSQL.

## Options

1. Copy everything the same number of times, as cells (a gateway plus its own services).
2. Copy only the part that is busy.
3. Add database replicas for reads.
4. Shard the database.

## Decision

1. Copy the busy part. In the platform that is the model service first, then the API.
2. Keep the API stateless, behind one load balancer, with a shared pool of copies instead of fixed cells.
3. Add one read replica, but only on separate hardware.
4. Do not shard.

## Why?

All lab numbers are measured at 100 users.

- **Tickets endpoint (the service is the busy part):**
  - Copying only the gateways: 25.5 → 26.6 req/s, so no real gain.
  - Copying the busy service as well: 55.9 req/s.
- **Users endpoint (the gateway is the busy part):** more gateways gave about 3× the throughput.
- **Database steps on one laptop:** no gain, because no new hardware was added.
  - Replicas: 12.7 vs 13.1 req/s.
  - Shards, writes: 61 vs 58 req/s.

## Trade-offs

- **We gain:** money goes only where it changes the result.
- **We lose:** every scaling decision needs a measurement first, and the replica's gain stays unproven until it runs on AWS.

## Future Trigger

- **Shard** (key: `organization_id`) when one primary cannot keep up with the writes or the data size, even on a bigger machine.
- **Add a second replica** when one replica's CPU becomes the busy part.
