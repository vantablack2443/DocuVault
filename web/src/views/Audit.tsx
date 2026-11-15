import { useState } from "react";

import { api, type AuditEntry, describeError, type Verification } from "../api";
import { formatTime, useLoad } from "../hooks";

const ACTIONS: Record<string, string> = {
  "gateway.request": "Gateway call",
  "auth.login": "Sign-in",
  "api_key.created": "API key created",
  "api_key.revoked": "API key revoked",
  "policy.updated": "Policy changed",
  "sandbox.created": "Sandbox created",
};

function actor(value: string): string {
  const [kind, id] = value.split(":");
  const short = id && id.length > 12 ? `${id.slice(0, 8)}…` : (id ?? "");
  return kind === "key" ? `API key ${short}` : kind === "user" ? `User ${short}` : value;
}

function summary(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join("; ");
}

export function Audit() {
  const entries = useLoad(() => api.audit());
  const [verification, setVerification] = useState<Verification | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exhausted, setExhausted] = useState(false);

  async function guarded(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setBusy(false);
    }
  }

  const verify = () => guarded(async () => setVerification(await api.verifyAudit()));
  const loadOlder = (rows: AuditEntry[]) =>
    guarded(async () => {
      const older = await api.audit(rows.at(-1)?.seq);
      setExhausted(older.length === 0);
      entries.setData([...rows, ...older]);
    });

  const rows = entries.data;
  const oldest = rows?.at(-1)?.seq;

  return (
    <section>
      <h1>Audit log</h1>
      <p className="intro">
        Every entry carries a fingerprint of the entry before it. Changing or removing one breaks
        the chain from that point on, and the check below finds where. The log holds counts and
        ids, never the personal data itself.
      </p>
      <div className="verify">
        <button type="button" className="primary" onClick={verify} disabled={busy}>
          Check the chain
        </button>
        {verification && (
          <p className={verification.valid ? "stamp ok" : "stamp broken"} role="status">
            {verification.valid
              ? `Chain intact: all ${verification.entries} entries match.`
              : `Chain broken at entry ${verification.first_invalid_seq}. Entries from there on cannot be trusted.`}
          </p>
        )}
      </div>
      {(error ?? entries.error) && (
        <p className="error" role="alert">
          {error ?? entries.error}
        </p>
      )}
      {rows && rows.length === 0 && <p className="empty">Nothing has been recorded yet.</p>}
      {rows && rows.length > 0 && (
        <>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th className="num">No.</th>
                  <th>Time</th>
                  <th>What happened</th>
                  <th>Who</th>
                  <th>Details</th>
                  <th>Fingerprint</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((entry) => (
                  <tr
                    key={entry.id}
                    className={
                      verification?.first_invalid_seq != null &&
                      entry.seq >= verification.first_invalid_seq
                        ? "untrusted"
                        : undefined
                    }
                  >
                    <td className="num">{entry.seq}</td>
                    <td>{formatTime(entry.created_at)}</td>
                    <td>{ACTIONS[entry.action] ?? entry.action}</td>
                    <td title={entry.actor}>{actor(entry.actor)}</td>
                    <td className="details">{summary(entry.details)}</td>
                    <td className="code" title={entry.hash}>
                      {entry.hash.slice(0, 12)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {oldest !== undefined && oldest > 1 && !exhausted && (
            <button type="button" className="quiet more" disabled={busy} onClick={() => loadOlder(rows)}>
              Show older entries
            </button>
          )}
        </>
      )}
    </section>
  );
}
