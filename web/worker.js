// Cloudflare Worker in front of the dashboard.
//
// Static files are served by Cloudflare directly; this code only runs for
// paths that are not a file. It forwards /api/* to the API so the browser
// talks to one address only. Content blockers stop cross-site requests to
// free hosting domains, which left visitors with a page that never loaded.

const PREFIX = "/api";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== PREFIX && !url.pathname.startsWith(`${PREFIX}/`)) {
      return env.ASSETS.fetch(request);
    }
    const target = new URL(url.pathname.slice(PREFIX.length) || "/", env.API_ORIGIN);
    target.search = url.search;

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
};
