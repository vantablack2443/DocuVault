// Keep-warm ping used by the Worker's Cron Trigger (worker.js "scheduled").
//
// While the API sleeps, the host answers a request from here with 502 at
// once instead of holding it until the container is up. So one ping is not
// enough to wake it: keep asking until it answers 200, for up to 3 minutes.
export const WAKE_ATTEMPTS = 18;
export const WAKE_PAUSE_MS = 10_000;

export async function keepWarm(env, pause = (ms) => new Promise((r) => setTimeout(r, ms))) {
  const probe = new URL("/healthz", env.API_ORIGIN);
  for (let attempt = 1; attempt <= WAKE_ATTEMPTS; attempt++) {
    let status;
    try {
      const response = await fetch(probe, {
        headers: { "user-agent": "docuvault-keep-warm/1.0", accept: "application/json" },
        signal: AbortSignal.timeout(90_000),
      });
      status = response.status;
    } catch (error) {
      status = `failed: ${error}`;
    }
    console.log(`keep-warm ${status} (attempt ${attempt})`);
    if (status === 200) return true;
    if (attempt < WAKE_ATTEMPTS) await pause(WAKE_PAUSE_MS);
  }
  return false;
}
