# ADR 0001: Guest access is a sandbox tenant, not a `guest` role

Status: accepted (2026-11-15)

## Context

The plan listed four roles: admin, member, auditor, guest. The demo must let a
recruiter see the whole product in under 30 seconds without signing up.

## Decision

There is no `guest` role. `POST /auth/guest` creates an isolated, expiring
tenant (`tenants.is_guest = true`, `expires_at` set) seeded with an admin, a
member and an auditor, and signs the visitor in as that tenant's admin.

## Why

- A read-only guest role would hide exactly the features the demo exists to
  show (API keys, policies, audit log).
- Limits belong to the sandbox, not to a person: budget caps and expiry are
  enforced per tenant (`is_guest`), so every credential in the sandbox,
  including API keys, is covered.
- Isolation is the ordinary tenant boundary, which is already tested, instead
  of a second permission system.

## Consequences

- Guest sessions get no refresh token; they end when the sandbox expires.
- Expired sandboxes are rejected at authentication and deleted (cascade) by
  the next guest login. No scheduler is needed on the free tier.
- Guest login is rate limited per client IP. Behind Render's proxy the IP must
  come from `X-Forwarded-For`; this is wired up in Phase 5 (deployment).
