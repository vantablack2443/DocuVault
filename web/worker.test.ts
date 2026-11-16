import { afterEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error plain JavaScript module without type declarations
import worker from "./worker.js";

const assets = { fetch: vi.fn(async () => new Response("asset")) };
const env = { ASSETS: assets, API_ORIGIN: "https://api.example", PROXY_SECRET: "s3cret" };

function upstream() {
  const spy = vi.fn(async (_url: URL, _init: RequestInit) => new Response("api"));
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
  assets.fetch.mockClear();
});

describe("worker", () => {
  it("serves everything outside /api from static assets", async () => {
    const spy = upstream();
    const response = await worker.fetch(new Request("https://site.example/apix/y"), env);
    expect(await response.text()).toBe("asset");
    expect(spy).not.toHaveBeenCalled();
  });

  it("forwards /api paths with query and method to the API", async () => {
    const spy = upstream();
    const request = new Request("https://site.example/api/usage?days=3", { method: "GET" });
    const response = await worker.fetch(request, env);
    expect(await response.text()).toBe("api");
    const [url, init] = spy.mock.calls[0]!;
    expect(String(url)).toBe("https://api.example/usage?days=3");
    expect(init.method).toBe("GET");
    expect(assets.fetch).not.toHaveBeenCalled();
  });

  it("passes the visitor address only from Cloudflare, with the secret", async () => {
    const spy = upstream();
    const request = new Request("https://site.example/api/auth/guest", {
      method: "POST",
      headers: {
        "cf-connecting-ip": "203.0.113.9",
        "x-docuvault-client-ip": "6.6.6.6",
        "x-docuvault-proxy-secret": "guess",
        authorization: "Bearer token",
      },
    });
    await worker.fetch(request, env);
    const headers = new Headers(spy.mock.calls[0]![1].headers);
    expect(headers.get("x-docuvault-client-ip")).toBe("203.0.113.9");
    expect(headers.get("x-docuvault-proxy-secret")).toBe("s3cret");
    expect(headers.get("authorization")).toBe("Bearer token");
  });

  it("sends no address claim when no secret is configured", async () => {
    const spy = upstream();
    const request = new Request("https://site.example/api/healthz", {
      headers: { "cf-connecting-ip": "203.0.113.9", "x-docuvault-client-ip": "6.6.6.6" },
    });
    await worker.fetch(request, { ...env, PROXY_SECRET: undefined });
    const headers = new Headers(spy.mock.calls[0]![1].headers);
    expect(headers.has("x-docuvault-client-ip")).toBe(false);
    expect(headers.has("x-docuvault-proxy-secret")).toBe(false);
  });

  it.each([
    "https://site.example/api//evil.example/steal",
    "https://site.example/api///evil.example",
    "https://site.example/api/%5Cevil.example",
    "https://site.example/api//",
  ])("never forwards to another host: %s", async (address) => {
    const spy = upstream();
    const response = await worker.fetch(new Request(address), env);
    for (const [url] of spy.mock.calls) {
      expect(new URL(String(url)).origin).toBe("https://api.example");
    }
    if (spy.mock.calls.length === 0) {
      expect(response.status).toBe(404);
    }
  });

  it("does not forward a path that would resolve to another host", async () => {
    const spy = upstream();
    const response = await worker.fetch(new Request("https://site.example/api//evil.example/x"), env);
    expect(response.status).toBe(404);
    expect(spy).not.toHaveBeenCalled();
  });

  it.each(["/api/docs", "/api/redoc", "/api/openapi.json", "/api/metrics"])(
    "keeps %s off the dashboard's address",
    async (path) => {
      const spy = upstream();
      const response = await worker.fetch(new Request(`https://site.example${path}`), env);
      expect(response.status).toBe(404);
      expect(spy).not.toHaveBeenCalled();
    },
  );

  it("pings only the API's liveness probe on the cron trigger", async () => {
    const spy = upstream();
    const pending: Promise<unknown>[] = [];
    await worker.scheduled({}, env, { waitUntil: (p: Promise<unknown>) => pending.push(p) });
    await Promise.all(pending);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0]?.[0])).toBe("https://api.example/healthz");
  });
});
