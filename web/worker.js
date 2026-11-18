// Cloudflare Worker in front of the dashboard.
//
// Static files are served by Cloudflare directly; this code only runs for
// paths that are not a file. It forwards /api/* to the API so the browser
// talks to one address only. Content blockers stop cross-site requests to
// free hosting domains, which left visitors with a page that never loaded.

import { keepWarm } from "./keepwarm.js";

const PREFIX = "/api";

// The API's interactive documentation loads scripts from a public CDN. Served
// under the dashboard's address those scripts could read a visitor's session,
// so the documentation stays on the API's own address.
const NOT_FORWARDED = new Set(["/docs", "/redoc", "/openapi.json", "/metrics"]);

function notFound() {
  return new Response('{"detail":"Not found"}', {
    status: 404,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== PREFIX && !url.pathname.startsWith(`${PREFIX}/`)) {
      return env.ASSETS.fetch(request);
    }
    const path = url.pathname.slice(PREFIX.length) || "/";
    // "/api//evil.example/x" must never turn into a request to evil.example.
    // The path is set on a URL that already has the API's host, and anything
    // that does not start with exactly one slash is refused outright.
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
      return notFound();
    }
    if (NOT_FORWARDED.has(path)) {
      return notFound();
    }
    const origin = new URL(env.API_ORIGIN);
    const target = new URL(origin);
    target.pathname = path;
    target.search = url.search;
    if (target.origin !== origin.origin) {
      return notFound();
    }

    const headers = new Headers(request.headers);
    headers.delete("host");
    // Never pass on what a visitor claims about themselves.
    headers.delete("x-docuvault-client-ip");
    headers.delete("x-docuvault-proxy-secret");
    const visitor = request.headers.get("cf-connecting-ip");
    if (env.PROXY_SECRET && visitor) {
      // The API believes this address only together with the shared secret.
      headers.set("x-docuvault-client-ip", visitor);
      headers.set("x-docuvault-proxy-secret", env.PROXY_SECRET);
    }

    return fetch(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
    });
  },

  // Cron Trigger (wrangler.jsonc): the free API host stops the container
  // after 15 minutes without traffic, and a cold start takes about a minute.
  // A request to the liveness probe every 10 minutes keeps it running.
  // GitHub's scheduler was too unreliable for this (runs hours late).
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(keepWarm(env));
  },
};
