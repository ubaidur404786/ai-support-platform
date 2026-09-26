# ADR-012 — Signed bearer tokens carrying only the user id

Status: accepted (v4)

## Context

Before v4 there was no notion of a caller. Once registration and login exist, every later
request has to prove who is making it. Two questions have to be answered together: what the
client presents on each request, and how much the server is willing to believe from it without
checking.

The platform runs as one process today and will later grow background workers and a separate
model service, so whatever is chosen should not assume a single shared in-memory session store.

## Options

1. **Server-side sessions with a cookie.** The classic design: a random id in a cookie, state in
   the database or Redis. Revocation is instant. Needs a lookup per request, a session store as a
   new component, and CSRF protection because cookies travel automatically.
2. **Opaque random tokens in a database table.** Like sessions but sent in a header. Instant
   revocation, one indexed lookup per request, no CSRF exposure. The token is meaningless by
   itself, so nothing can be read from it.
3. **A JWT carrying user id, organisation id and expiry.** No lookup at all: the signature is
   the proof. Fastest. Cannot be revoked before expiry, and any claim baked in goes stale —
   including organisation membership.
4. **A JWT carrying the user id and expiry only, with the user loaded per request.** One
   primary-key lookup, but `is_active` and organisation membership are always current.
5. **An external identity provider** (Auth0, Keycloak, Cognito). Correct for a real product,
   ruled out here: paid services are out of scope, and running Keycloak locally would add a
   container and a large amount of configuration to learn nothing this project is about.

## Decision

Option 4.

- `POST /auth/login` returns a JWT signed with HS256, containing `sub` (the user id as a string),
  `exp` and `iat`.
- Clients send `Authorization: Bearer <token>`.
- `get_current_user` verifies the signature and expiry, loads the `users` row, and rejects the
  request if the user is missing or `is_active` is false.
- `jwt_secret_key` is a **required** setting with no default, so the application refuses to start
  rather than run with a guessable key.
- `jwt.decode(..., algorithms=[settings.jwt_algorithm])` — the accepted algorithm list is ours,
  never the token's.
- Token lifetime is 60 minutes, configurable.

## Why?

- **No session store to add.** Options 1 and 2 introduce a component whose only job is holding
  session state. A signed token needs nothing new, which matters in a project whose rule is that
  every component must be forced by a problem.
- **Option 3 was rejected on a trade-off we could measure.** Baking `organization_id` into the
  token saves one query; it also means a user disabled a minute ago keeps working for up to an
  hour, and a user moved between organisations keeps the old one. Measured, the lookup we kept
  costs **0.183 ms** (`Index Scan using users_pkey`) against a 33.8 ms request — 0.5%. Paying
  0.5% for `is_active` to mean what it says is not a hard decision.
- **A header rather than a cookie** removes CSRF from scope: a header is not attached
  automatically by the browser, so a cross-site form post cannot carry it.
- **HS256 rather than RS256**, because one process signs and verifies. RS256's advantage — other
  services verifying with a public key while only the issuer holds the private key — has no
  consumer yet. It becomes right when the model service or a worker needs to check tokens.
- **The algorithm list is fixed by us** because accepting the algorithm named *inside* the token
  is the classic JWT vulnerability: a token claiming `"alg": "none"` would otherwise verify with
  no signature at all.
- **One error for every failure.** Expired, tampered with, signed by another key, unknown user,
  disabled user — all produce `401 "Invalid or expired token"`. Distinguishing them helps only an
  attacker. Storage failure is the exception: that is 503, because it means we could not
  determine who the caller is, which is our problem and not their credentials'.

## Trade-offs

Gain: no new infrastructure; stateless verification; immediate effect for disabled accounts; no
CSRF surface; a token that later services can verify without calling this one.

Lose:

- **A token cannot be revoked before it expires.** This is the defining weakness of the approach.
  `is_active` covers the case where we disable the *user*; it does nothing about a token stolen
  from an account that is still legitimately active. For up to 60 minutes, that token works.
- **The payload is readable by anyone holding the token.** A JWT is signed, not encrypted. Nothing
  secret may ever go in it — and confirming this by base64-decoding a real token is part of the
  version's test procedure, so the property is demonstrated rather than trusted.
- **A leaked signing key forges every account at once.** Sessions leak one session; a key leaks
  the whole system. Hence no default value and a hard startup failure.
- **`/auth/login` became the most expensive endpoint in the system** — 682 ms, all bcrypt, and
  reachable without credentials. Not caused by this decision (see ADR-014) but exposed by it.
- **60 minutes is a guess.** It is short enough that a stolen token expires within a working
  session and long enough to avoid needing refresh tokens yet. No measurement supports the exact
  number, and it is configuration rather than code for that reason.

## Future Trigger

- **Add refresh tokens** when 60 minutes becomes user-hostile — the point at which people are
  re-entering passwords during normal work.
- **Add a revocation list** (a small Redis set of blocked token ids, checked per request) when
  "log out everywhere" or "this token was stolen" becomes a real requirement. That trades
  statelessness for control, which is the whole point of the choice made here, so it needs a
  reason.
- **Switch to RS256** when a second service must verify tokens — the model service in v13, or a
  background worker. Sharing one symmetric secret across services means every service can also
  *issue* tokens.
- **Revisit the per-request lookup** if profiling ever shows it mattering. At 0.5% of a request
  it does not, and the number is recorded here so the claim can be rechecked rather than
  remembered.
- **Reconsider cookies** if a browser front end makes header management awkward — at which point
  CSRF protection comes back into scope.
