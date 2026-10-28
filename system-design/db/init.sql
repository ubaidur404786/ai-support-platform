-- Runs once, the first time the database container starts with an empty disk.
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

-- 1,000 users and 10,000 tickets of test data.
INSERT INTO users (name, email)
SELECT 'user ' || n, 'user' || n || '@example.com'
FROM generate_series(1, 1000) AS n;

INSERT INTO tickets (user_id, text)
SELECT 1 + (n % 1000),
       CASE WHEN n % 3 = 0 THEN 'I want a refund for order ' || n
            ELSE 'How do I change my password? ticket ' || n END
FROM generate_series(1, 10000) AS n;
