# ADR-017 — Rate-limit keys: client address for auth, user id for inference

Status: accepted (v5)

## Context

A rate limit is only as good as its key: the thing we count *per*. A key that is too broad punishes
innocent callers together; a key the caller controls lets them escape the limit entirely.

v5 limits two different kinds of expensive work:

- **`/auth/login` and `/auth/register`** — bcrypt, ~890 ms each, necessarily anonymous.
- **`/classify` and `POST /tickets`** — model inference. `/classify` was public in v4.

## Options

1. **Client address (`request.client.host`)** everywhere.
2. **`X-Forwarded-For` header** — the "real" client address as reported by a proxy.
3. **User id** everywhere — impossible for login, where there is no user yet.
4. **The email being logged into** — per-account limit on login attempts.
5. **Address for auth, user id for inference**, with `/classify` requiring a token.

## Decision

Option 5.

- `limit_auth_attempts` keys on `request.client.host`, and is declared on the **auth router**, so
  every current and future `/auth` endpoint spends the one "auth" budget (10/min).
- `limit_inference` keys on `user:<id>`, after `get_current_user`. `/classify` and `POST /tickets`
  share one "inference" budget (60/min).
- `POST /classify` **requires a token** from this version.
- `X-Forwarded-For` is **deliberately ignored**.

## Why?

- **Login has no user, so the address is the only honest key.** The caller is trying to *obtain*
  an identity.
- **Inference has a user, and the user is the better key.** An address is shared by everyone behind
  an office NAT — ten Acme agents would share one budget. A user id cannot be changed by switching
  networks. That is only possible if `/classify` knows who is calling, so it now requires a token;
  keeping it public would have forced it onto the weaker address key.
- **Authentication runs before the limit**, so an anonymous caller gets 401 and cannot spend (or
  probe) anyone's budget, and does not learn whether the model is loaded.
- **One budget for both inference routes.** They spend the same resource. Separate budgets would
  double the allowance and make each route a way around the other's limit — tested explicitly.
- **`X-Forwarded-For` is written by the client.** With no proxy in front, trusting it would let a
  script send a new invented address on every request and never be limited. It becomes correct
  only when a proxy we control sets it — and then only the proxy's entry may be trusted.
- **Not per-email on login (option 4), yet.** It would slow a slow, distributed guess at one account,
  but it also lets anyone lock a victim out by failing logins for their email on purpose. That is a
  real design question with a real cost, and no measurement asks for it yet.

## Trade-offs

Gain: callers who share a network do not share an inference budget; the inference limit cannot be
escaped by changing address; the anonymous path stays cheap to refuse.

Lose:

- **A per-user refusal is not free** — the token check and `users` lookup run first. Measured:
  **14.8 ms** P50 for a refused `/classify` vs **6.2 ms** for a refused (address-keyed) login.
- **Shared-address users share the auth budget.** Colleagues behind one NAT logging in within the
  same minute compete for 10 attempts.
- **Behind a reverse proxy, every caller would have the proxy's address** — one budget for the
  whole world — until `X-Forwarded-For` is trusted from that proxy.
- **Breaking change**: an anonymous `/classify` client now gets 401.
- **Does not stop distributed attacks** on login — many addresses each stay under their limit.

## Future Trigger

- **A reverse proxy or load balancer is introduced** — read the client address from the proxy's
  `X-Forwarded-For` entry (e.g. uvicorn `--proxy-headers` with `--forwarded-allow-ips` set to the
  proxy only).
- **Targeted guessing at one account is observed** — add a per-email limit on failed logins, with
  a design that does not let an attacker lock the owner out (e.g. slow down rather than block,
  or exempt a previously successful address).
- **Organisations need different allowances** (plans, quotas) — key inference on the organisation
  as well as the user, with limits stored per organisation.
