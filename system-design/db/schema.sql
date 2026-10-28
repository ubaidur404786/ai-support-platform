-- Tables and test data. Run by db/init.sh with two settings:
--   shard_count / shard_index  - which tickets this database keeps.
-- Without sharding: shard_count = 1, shard_index = 0, so it keeps all of them.
CREATE TABLE users (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL,
    email TEXT NOT NULL
);

CREATE TABLE tickets (
    id      SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    text    TEXT NOT NULL
);

-- Users are small and needed everywhere, so every shard has all 1,000.
INSERT INTO users (name, email)
SELECT 'user ' || n, 'user' || n || '@example.com'
FROM generate_series(1, 1000) AS n;

-- 200,000 tickets in total; a shard keeps only those whose user_id % shard_count = shard_index.
-- Ticket ids are unique across shards because they come from the same series.
INSERT INTO tickets (id, user_id, text)
SELECT n,
       1 + (n % 1000),
       CASE WHEN n % 3 = 0 THEN 'I want a refund for order ' || n
            ELSE 'How do I change my password? ticket ' || n END
FROM generate_series(1, 200000) AS n
WHERE (1 + (n % 1000)) % :shard_count = :shard_index;

-- New tickets get ids above the seeded ones.
SELECT setval('tickets_id_seq', 200000);
