# ADR-014 — bcrypt, used directly, for password storage

Status: accepted (v4)

## Context

Registration means the platform holds a secret belonging to someone else. If the `users` table is
ever read — a leaked backup, a SQL injection, a misconfigured replica, an engineer with more access
than they need — nothing in it may reveal anyone's password. People reuse passwords, so a breach
here is a breach of their email and bank too.

This is the one decision in the project with no local-alternative escape hatch: there is no
"simpler version that is good enough for a learning project".

## Options

**What to store:**

1. **The password.** Not an option. Recorded only to be explicit that it was never one.
2. **A fast cryptographic hash** (SHA-256). One-way, and useless here: a GPU computes billions per
   second, so an eight-character password falls in minutes.
3. **A salted fast hash.** Defeats precomputed rainbow tables but not brute force, because the
   hash is still cheap to compute.
4. **A deliberately slow, salted hash** — bcrypt, scrypt or argon2. Tunable cost, so the defender
   pays milliseconds and the attacker pays years.

**Which library:**

5. **`passlib[bcrypt]`.** The usual recommendation. A wrapper supporting many algorithms, with a
   known incompatibility between its 1.7.x releases and modern `bcrypt` versions.
6. **`bcrypt` directly.** One algorithm, one dependency, an API of two functions.
7. **`argon2-cffi`.** Winner of the Password Hashing Competition and the current best-practice
   choice; memory-hard, which resists GPU attacks better than bcrypt.

## Decision

Option 4, implemented as option 6: the `bcrypt` package used directly, at its default cost factor
of 12.

- `hash_password()` calls `bcrypt.hashpw(password, bcrypt.gensalt())`; the salt and cost are stored
  inside the returned string, so verification needs nothing but the one column.
- `verify_password()` calls `bcrypt.checkpw()` and returns `False` on `ValueError`, so a corrupt
  value in the column is a failed login rather than a 500.
- The column is named `password_hash`, never `password`.
- Input over **72 bytes** is rejected at the API boundary by a validator on `RegisterRequest`.
- When an email does not exist, login still verifies against a throwaway hash before returning 401.
- No response schema in `app/auth/schemas.py` has any password field, and a test asserts the raw
  response text of register and login contains neither `"password"` nor `"$2b$"`.

## Why?

- **Slowness is the security property.** bcrypt at cost 12 measured **682.2 ms per verification**
  on this machine. That is not a performance problem to be optimised away; it is the feature. An
  attacker with the table gets roughly 1.5 guesses per second per core instead of billions.
- **A per-password salt, not a global one.** `gensalt()` is called per password, so two users with
  the same password have different hashes. An attacker cannot see that they match, and cracking one
  does not crack the other. A test asserts this directly rather than trusting it.
- **`passlib` was rejected on maintenance grounds, not taste.** It abstracts over a dozen
  algorithms this project will never use, and its version incompatibility with modern `bcrypt` is a
  real trap. Fewer layers between the code and the primitive.
- **`argon2` (option 7) is arguably the better algorithm and was still not chosen.** bcrypt is
  sufficient at this scale, universally available, and its cost model is simpler to explain and to
  reason about while learning. This is a deliberate "good enough, understood" over "best,
  partially understood" — and it is recorded as such so the trade can be revisited honestly rather
  than defended as optimal.
- **The 72-byte limit is enforced rather than ignored.** bcrypt hashes at most the first 72 bytes
  and **silently discards the rest**, so two passwords sharing a 72-byte prefix would be
  interchangeable — a user with a long passphrase would have less security than they believe.
  Pydantic's `max_length` counts *characters*, not bytes, so a custom validator measures
  `len(value.encode("utf-8"))`. Thirty accented characters are ninety bytes; a character limit
  would have let that through.
- **Constant-time comparison comes free with `checkpw()`.** A plain `==` leaks information through
  how long it takes to find the first differing byte.
- **Timing equalisation on an unknown email.** Returning early for an address that does not exist
  would make it measurably faster than a real address with a wrong password, turning response time
  into a user-enumeration oracle. Verifying against a dummy hash costs 682 ms we do not need, to
  avoid leaking who has an account.
- **A minimum length of 12 characters and no composition rules.** Length is the password rule with
  evidence behind it; mandatory symbols and digits mostly produce `Password1!`.

## Trade-offs

Gain: a stolen `users` table is expensive rather than catastrophic; identical passwords are
indistinguishable in storage; the storage format carries its own salt and cost, so the cost factor
can be raised later without a migration; no plaintext password exists anywhere except one line of
`service.py`.

Lose:

- **`/auth/login` is now the most expensive endpoint in the system**, at 682 ms P50 — roughly 30×
  a ticket write — and it is reachable **without credentials**. This decision created a
  denial-of-service surface: one anonymous request buys 682 ms of CPU. Per-caller rate limiting is
  the answer and does not exist yet.
- **The test suite went from ~7 s to 102.92 s.** Around 45 fixtures each register (one hash) and
  log in (one verify), ~1.36 s apiece: `45 × 1.36 ≈ 61 s`, about 60% of the runtime. The two
  standard fixes both cost something real — a lower cost factor in tests means no longer testing
  the production configuration; session-scoping the registration means tests share a user.
- **The cost factor is a moving target.** 12 is appropriate on 2026 hardware. It has to be raised
  as hardware improves, and nothing in the system reminds anyone to do it.
- **Existing hashes are not upgraded when the cost factor changes.** Re-hashing on next successful
  login is the standard technique, and it is not implemented.
- **682 ms is this machine.** On a faster server the same cost factor gives weaker protection, and
  on a slower one, worse latency. The number is recorded with its conditions for that reason.

## Future Trigger

- **Add rate limiting on `/auth/login`** before this is reachable from anywhere but localhost. This
  is the most pressing item created by this decision.
- **Lower the cost factor in tests only** (`BCRYPT_ROUNDS` as a setting) when the suite's runtime
  starts discouraging people from running it. 103 s is close to that line.
- **Re-hash on login** when the cost factor is first raised, so existing users migrate without
  being asked to change anything.
- **Reconsider argon2** if the threat model grows — a real user base, or an attacker plausibly
  holding GPUs. bcrypt's weakness relative to argon2 is that it is not memory-hard.
- **Add password reset, logout and rotation** when there are users who are not the owner of this
  repository. All three are missing and all three are real.
- **Re-measure the 682 ms** whenever the deployment target changes. The number is meaningless
  without the machine it was taken on.
