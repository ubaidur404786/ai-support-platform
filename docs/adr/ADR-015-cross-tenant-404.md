# ADR-015 — A read across a tenant boundary answers 404, not 403

Status: accepted (v4)

## Context

ADR-013 establishes that a ticket belongs to exactly one organisation and that every read is
filtered by it. That leaves a separate question, small in code and significant in consequence:
when Maya at Acme requests ticket 47, which belongs to Globex, what does the server say?

The two obvious answers are both technically defensible, which is why this is its own record.
`GET /tickets/{id}` also already returns 404 for an id that never existed, so the choice
determines whether those two situations are distinguishable from outside.

## Options

1. **403 Forbidden.** The literal truth: the resource exists and this caller may not have it.
   HTTP's own definition of the status fits.
2. **404 Not Found.** Indistinguishable from an id that was never used.
3. **401 Unauthorized.** Wrong: the caller is authenticated and we know exactly who they are.
   Recorded only to rule it out.
4. **200 with an empty or redacted body.** Avoids saying anything, and breaks the contract that a
   200 means the requested resource is in the response.

## Decision

Option 2. Another organisation's ticket is reported as **404 Not Found**, with the same message a
never-existing id produces: `"Ticket 47 not found"`.

This is implemented structurally rather than as a decision in the handler. The repository's `get()`
filters on both `id` and `organization_id` and returns `None` when either fails to match; the
router turns `None` into 404 without knowing which of the two reasons applied. **The handler cannot
leak the distinction, because it never learns it.**

The list endpoint follows the same principle: `total` counts only the caller's organisation, so a
tenant with no tickets sees `total: 0` rather than a count it cannot read.

## Why?

- **Across a tenant boundary, existence is itself information.** A 403 confirms that ticket 47 is
  real. Iterate the ids, count the 403s, and you have measured a competitor's ticket volume,
  growth rate, and — by timing your probes — when they are busy. None of that requires reading a
  single ticket body. This is the whole argument, and it is why the literally-correct status is the
  wrong one.
- **The two situations are genuinely indistinguishable from the caller's side, so the API should
  say so.** From Acme's perspective there is no ticket 47. Not "there is one you cannot see" —
  there is nothing, because visibility is what "exists" means at an API boundary.
- **404 is HTTP-legitimate, not a lie.** The specification permits a server to answer 404 when it
  does not wish to reveal that a resource exists, and names exactly this case. This is a supported
  use of the status, not an abuse of it.
- **Implemented as a structure rather than a rule.** Returning `None` from a query that filters on
  both columns means there is no branch anywhere that could accidentally answer 403. A handler
  written later cannot get it wrong, because the information needed to get it wrong never reaches it.
- **403 remains available and meaningful for a different question.** When roles arrive — a member
  who may read but not review — 403 will be correct, because both parties already agree the
  resource is within the caller's organisation. Spending 403 on the tenant boundary now would blur
  a distinction worth keeping: **401 is "I do not know who you are", 403 is "I know, and no", 404 is
  "there is nothing here for you".**

## Trade-offs

Gain: a tenant cannot probe for the existence of another tenant's records; one code path for both
cases, so no branch can regress; 403 stays free for genuine in-organisation permission decisions.

Lose:

- **Debugging is harder.** A misconfigured client sees 404 and cannot tell "wrong id" from "wrong
  organisation". The person diagnosing it needs database access or server logs. Real cost, paid
  deliberately.
- **A support engineer cannot answer "does this ticket exist?" through the API.** They need another
  tool. That tool then needs its own authorization story, which is a future problem this decision
  quietly creates.
- **Timing may still leak a little.** A row that exists but is filtered out takes a slightly
  different path through PostgreSQL than an id with no row at all. At the measured 0.128 ms page
  cost the difference is far below network jitter, but it is not a formal guarantee — 404 makes
  enumeration impractical rather than provably impossible.
- **It reads as evasive to a client developer**, who may reasonably wonder why they get 404 for a
  ticket a colleague can see. The answer belongs in API documentation that does not exist yet.

## Future Trigger

- **Use 403 when roles arrive inside an organisation**, where the caller and the server agree the
  resource is theirs and the question is only whether this person may act on it. That is the
  distinction this ADR is protecting.
- **Apply the same rule to documents** in v5 and to every owned entity after it. A cross-tenant
  document read must be 404 for the same reason, and the standing obligation is identical to the
  page-size rule in ADR-011.
- **Revisit if an audit or compliance requirement demands that denied access be reported as
  denied.** Some regimes want a distinguishable, logged refusal rather than a silent 404. The
  resolution is a server-side audit log, not a change to the response.
- **Add logging of cross-tenant attempts.** A denied read is currently indistinguishable from a
  typo in the server's own logs too, which means a real probing attack would be invisible. Worth
  fixing when observability arrives in v20.
