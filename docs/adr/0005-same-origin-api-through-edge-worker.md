# ADR 0005: The dashboard reaches the API through its own address

Date: 2026-11-15. Status: accepted.

## Context

The dashboard (Cloudflare) called the API (Render) directly at its
`onrender.com` address. On the first day of the public demo this failed in a
browser with uBlock Origin: the content blocker stops cross-site requests to
that free hosting domain. The page showed "waking the server" forever. Opening
the API address directly worked, so nothing was wrong with either service.
Developers and technical recruiters, the audience of this demo, often run a
content blocker.

## Decision

The Cloudflare Worker that serves the dashboard also forwards `/api/*` to the
API (`web/worker.js`). The browser talks to one address only.

The Worker runs only for paths that are not a static file. It is a plain
forwarder: no caching, no changes to bodies, streaming passes through.

Rate limits need the visitor's address, and the API now sees the Worker as the
caller. The Worker states the address Cloudflare observed in
`X-DocuVault-Client-IP` and proves its identity with `X-DocuVault-Proxy-Secret`
(`PROXY_SECRET`, set on both sides). The API ignores the address without the
right secret, and the Worker strips both headers from incoming requests, so a
visitor cannot choose their own address. Without a configured secret the API
falls back to `X-Forwarded-For` and `TRUSTED_PROXY_HOPS`.

## Consequences

- No cross-site request from the dashboard; CORS is only needed for local
  development and for other front ends.
- The API stays reachable directly for API-key clients (`/v1/...`); that is its
  intended use and is unaffected.
- One more moving part with a free-plan limit (100,000 Worker requests a day)
  and one more secret to keep in sync.
- Until `PROXY_SECRET` is set on both sides, dashboard visitors may share one
  guest sign-in limit.
- Redacted prompts and answers pass through Cloudflare. They did before as
  well: Render's own front door is Cloudflare.
