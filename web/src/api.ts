// Typed client for the DocuVault API. Tokens live in sessionStorage: they end
// with the browser tab, which fits a demo session.

import type { Entity } from "./text";

// Direct address of the API's liveness probe. Opening it in a tab wakes the
// sleeping free host, which a request through the Worker does not always do.
// Unset in local development, where nothing sleeps.
export const WAKE_URL: string | undefined = import.meta.env.VITE_WAKE_URL || undefined;

export const API_URL: string = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export type Role = "admin" | "member" | "auditor";

export interface CurrentUser {
  id: string;
  tenant_id: string;
  email: string;
  role: Role;
  is_guest: boolean;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string | null;
  expires_in: number;
}

export interface PlaygroundResult {
  original: string;
  entities: Entity[];
  sent_upstream: string;
  upstream_answer: string;
  final_answer: string;
  provider: string;
  cache_hit: boolean;
}

export interface UsageDay {
  day: string;
  requests: number;
  cache_hits: number;
  redactions: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface RequestRow {
  id: string;
  created_at: string;
  model: string;
  provider: string;
  status: string;
  stream: boolean;
  cache_hit: boolean;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  redaction_counts: Record<string, number>;
}

export interface AuditEntry {
  id: string;
  seq: number;
  actor: string;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
  prev_hash: string;
  hash: string;
}

export interface Verification {
  valid: boolean;
  entries: number;
  first_invalid_seq: number | null;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Turn a failed call into a sentence that says what to do next. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 0:
        return "The server did not answer. Check your connection and try again.";
      case 401:
        return "Your session has ended. Start a new one to continue.";
      case 403:
        return "Your role does not allow this.";
      case 422:
        return "The server rejected the input. Check the length and try again.";
      case 429:
        return "Too many requests. Wait a few seconds and try again.";
      case 502:
        return "No language model is reachable right now. Try again in a minute.";
      default:
        return `The server answered with an error (${error.status}). Try again.`;
    }
  }
  return "Something went wrong in the browser. Reload the page and try again.";
}

const ACCESS = "docuvault.access";
const REFRESH = "docuvault.refresh";

function read(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(tokens: TokenResponse | null): void {
  try {
    if (tokens === null) {
      sessionStorage.removeItem(ACCESS);
      sessionStorage.removeItem(REFRESH);
      return;
    }
    sessionStorage.setItem(ACCESS, tokens.access_token);
    if (tokens.refresh_token) {
      sessionStorage.setItem(REFRESH, tokens.refresh_token);
    } else {
      sessionStorage.removeItem(REFRESH);
    }
  } catch {
    // Storage blocked: the session then lasts until the page reloads.
  }
  memory = tokens ? { access: tokens.access_token, refresh: tokens.refresh_token } : null;
}

let memory: { access: string; refresh: string | null } | null = null;

function tokens(): { access: string; refresh: string | null } | null {
  if (memory) {
    return memory;
  }
  const access = read(ACCESS);
  return access ? { access, refresh: read(REFRESH) } : null;
}

export function hasSession(): boolean {
  return tokens() !== null;
}

let onSessionEnd: () => void = () => {};
export function setSessionEndHandler(handler: () => void): void {
  onSessionEnd = handler;
}

async function send(path: string, init: RequestInit, access?: string): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (access) {
    headers.set("Authorization", `Bearer ${access}`);
  }
  // Right after the free host wakes up, its front door can answer 502 or 503 for
  // a few seconds although the server is about to be ready. Those answers come
  // from the proxy, not from the application, so repeating the request is safe.
  for (let attempt = 0; ; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(`${API_URL}${path}`, { ...init, headers });
    } catch {
      throw new ApiError(0, "network");
    }
    const starting = response.status === 502 || response.status === 503;
    const fromProxy = !(response.headers.get("content-type") ?? "").includes("application/json");
    if (!starting || !fromProxy || attempt >= STARTUP_RETRIES) {
      return response;
    }
    await new Promise((resolve) => setTimeout(resolve, STARTUP_RETRY_DELAY_MS));
  }
}

const STARTUP_RETRIES = 5;
const STARTUP_RETRY_DELAY_MS = 2000;

let refreshing: Promise<boolean> | null = null;

/** Refresh tokens rotate, so concurrent 401s must share one refresh call. */
function refreshOnce(refreshToken: string): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const response = await send("/auth/refresh", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!response.ok) {
        return false;
      }
      store((await response.json()) as TokenResponse);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let current = tokens();
  let response = await send(path, init, current?.access);
  if (response.status === 401 && current?.refresh && (await refreshOnce(current.refresh))) {
    current = tokens();
    response = await send(path, init, current?.access);
  }
  if (response.status === 401 && current) {
    store(null);
    onSessionEnd();
  }
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  /** Resolves once the server answers; a sleeping free-tier container can take a minute. */
  async wake(signal: AbortSignal): Promise<void> {
    for (;;) {
      try {
        const response = await fetch(`${API_URL}/healthz`, { signal });
        if (response.ok) {
          return;
        }
      } catch (error) {
        if (signal.aborted) {
          throw error;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  },
  async guestLogin(): Promise<void> {
    store(await call<TokenResponse>("/auth/guest", { method: "POST" }));
  },
  async login(email: string, password: string): Promise<void> {
    store(await call<TokenResponse>("/auth/login", post({ email, password })));
  },
  async logout(): Promise<void> {
    const refresh = tokens()?.refresh;
    store(null);
    if (refresh) {
      await send("/auth/logout", post({ refresh_token: refresh })).catch(() => undefined);
    }
  },
  me: () => call<CurrentUser>("/auth/me"),
  playground: (message: string) => call<PlaygroundResult>("/playground/chat", post({ message })),
  usage: (days = 14) => call<UsageDay[]>(`/usage?days=${days}`),
  requests: (limit = 25) => call<RequestRow[]>(`/requests?limit=${limit}`),
  audit: (beforeSeq?: number) =>
    call<AuditEntry[]>(`/audit?limit=25${beforeSeq ? `&before_seq=${beforeSeq}` : ""}`),
  verifyAudit: () => call<Verification>("/audit/verify"),
  apiKeys: () => call<ApiKey[]>("/api-keys"),
  createApiKey: (name: string) => call<ApiKeyCreated>("/api-keys", post({ name })),
  revokeApiKey: (id: string) => call<void>(`/api-keys/${id}`, { method: "DELETE" }),
  policy: () => call<{ enabled_types: string[] }>("/policy"),
  setPolicy: (enabled_types: string[]) =>
    call<{ enabled_types: string[] }>("/policy", {
      method: "PUT",
      body: JSON.stringify({ enabled_types }),
    }),
};
