import { type FormEvent, useState } from "react";

import { api, API_URL, type ApiKeyCreated, describeError } from "../api";
import { formatTime, useLoad } from "../hooks";
import { PII_TYPES, typeLabel } from "../text";

export function Settings({ canEdit }: { canEdit: boolean }) {
  return (
    <section>
      <h1>Keys and policy</h1>
      {!canEdit && <p className="intro">Your role can read these settings. Only admins change them.</p>}
      <Policy canEdit={canEdit} />
      <Keys canEdit={canEdit} />
    </section>
  );
}

function Policy({ canEdit }: { canEdit: boolean }) {
  const policy = useLoad(api.policy);
  const [draft, setDraft] = useState<Set<string> | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const saved = new Set(policy.data?.enabled_types ?? []);
  const selected = draft ?? saved;
  const dirty = draft !== null && (draft.size !== saved.size || [...draft].some((t) => !saved.has(t)));

  function toggle(type: string) {
    const next = new Set(selected);
    if (!next.delete(type)) {
      next.add(type);
    }
    setDraft(next);
    setStatus(null);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      policy.setData(await api.setPolicy([...selected]));
      setDraft(null);
      setStatus("Policy saved. It applies to the next call.");
    } catch (reason) {
      setError(describeError(reason));
    }
  }

  return (
    <form className="panel" onSubmit={save}>
      <h2>What gets replaced</h2>
      <p className="hint">Kinds that are switched off pass through to the model unchanged.</p>
      {policy.error && (
        <p className="error" role="alert">
          {policy.error}
        </p>
      )}
      {policy.data && (
        <fieldset disabled={!canEdit}>
          <legend className="sr-only">Kinds of personal data to replace</legend>
          {PII_TYPES.map((type) => (
            <label key={type} className="check">
              <input type="checkbox" checked={selected.has(type)} onChange={() => toggle(type)} />
              {typeLabel(type)}
            </label>
          ))}
        </fieldset>
      )}
      {canEdit && (
        <div className="row">
          <button type="submit" className="primary" disabled={!dirty}>
            Save policy
          </button>
          {status && <span role="status">{status}</span>}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function Keys({ canEdit }: { canEdit: boolean }) {
  const keys = useLoad(api.apiKeys);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function guarded(action: () => Promise<void>) {
    setError(null);
    try {
      await action();
    } catch (reason) {
      setError(describeError(reason));
    }
  }

  function create(event: FormEvent) {
    event.preventDefault();
    void guarded(async () => {
      setCreated(await api.createApiKey(name.trim()));
      setCopied(false);
      setName("");
      keys.reload();
    });
  }

  const revoke = (id: string) =>
    guarded(async () => {
      await api.revokeApiKey(id);
      if (created?.id === id) {
        setCreated(null);
      }
      keys.reload();
    });

  const curl = created
    ? `curl ${API_URL}/v1/chat/completions \\\n  -H "Authorization: Bearer ${created.key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "docuvault-default", "messages": [{"role": "user", "content": "Frau Schmidt bittet um Rückruf."}]}'`
    : "";

  return (
    <div className="panel">
      <h2>API keys</h2>
      <p className="hint">
        A key lets an application call the gateway the same way it would call the OpenAI API.
      </p>
      {canEdit && (
        <form className="row" onSubmit={create}>
          <label className="sr-only" htmlFor="key-name">
            Name for the new key
          </label>
          <input
            id="key-name"
            placeholder="For example: support tool"
            maxLength={80}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <button type="submit" className="primary" disabled={name.trim() === ""}>
            Create key
          </button>
        </form>
      )}
      {created && (
        <div className="secret" role="status">
          <p>
            <strong>Copy this key now.</strong> It is shown once; only a fingerprint is stored.
          </p>
          <div className="row">
            <code>{created.key}</code>
            <button
              type="button"
              onClick={() =>
                void navigator.clipboard.writeText(created.key).then(
                  () => setCopied(true),
                  () => setError("Copying was blocked by the browser. Select the key and copy it by hand."),
                )
              }
            >
              {copied ? "Copied" : "Copy key"}
            </button>
          </div>
          <p className="hint">Try it from a terminal:</p>
          <pre>{curl}</pre>
        </div>
      )}
      {(error ?? keys.error) && (
        <p className="error" role="alert">
          {error ?? keys.error}
        </p>
      )}
      {keys.data && keys.data.length === 0 && (
        <p className="empty">No keys yet.{canEdit && " Create one to connect an application."}</p>
      )}
      {keys.data && keys.data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Starts with</th>
                <th>Created</th>
                <th>Last used</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.data.map((key) => (
                <tr key={key.id} className={key.revoked_at ? "revoked" : undefined}>
                  <td>{key.name}</td>
                  <td className="code">dv_{key.prefix}</td>
                  <td>{formatTime(key.created_at)}</td>
                  <td>{key.last_used_at ? formatTime(key.last_used_at) : "never"}</td>
                  <td>
                    {key.revoked_at ? (
                      "Revoked"
                    ) : canEdit ? (
                      <button type="button" className="quiet danger" onClick={() => void revoke(key.id)}>
                        Revoke key
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
