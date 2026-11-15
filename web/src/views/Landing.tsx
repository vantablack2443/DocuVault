import { type FormEvent, useState } from "react";

import { api, ApiError, describeError } from "../api";

// The opening demonstration: a customer message whose personal data turns
// into placeholders. All values are invented.
const LETTER: Array<string | [original: string, placeholder: string]> = [
  "Guten Tag, hier ist ",
  ["Anna Schmidt", "<PERSON_1>"],
  ". Bitte überweisen Sie die Erstattung auf ",
  ["DE89 3704 0044 0532 0130 00", "<IBAN_1>"],
  " und schicken Sie die Bestätigung an ",
  ["a.schmidt@example.de", "<EMAIL_1>"],
  ". Rückfragen gern unter ",
  ["0681 123456", "<PHONE_1>"],
  ".",
];

export function Landing({ ready, onSignedIn }: { ready: boolean; onSignedIn: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function run(action: () => Promise<void>, wrongLogin?: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onSignedIn();
    } catch (reason) {
      const rejected = reason instanceof ApiError && reason.status === 401 && wrongLogin;
      setError(rejected || describeError(reason));
      setBusy(false);
    }
  }

  function signIn(event: FormEvent) {
    event.preventDefault();
    void run(() => api.login(email, password), "E-mail or password is wrong.");
  }

  return (
    <main className="landing">
      <p className="wordmark">DocuVault</p>
      <h1>Names, IBANs and addresses are replaced before a prompt leaves your network.</h1>
      <p className="letter" aria-label="Example: a customer message before and after redaction">
        {LETTER.map((part, index) =>
          typeof part === "string" ? (
            part
          ) : (
            <span className="swap" key={index} style={{ animationDelay: `${0.9 + index * 0.25}s` }}>
              <span className="swap-original">{part[0]}</span>
              <span className="swap-placeholder">{part[1]}</span>
            </span>
          ),
        )}
      </p>
      <p className="lede">
        DocuVault sits between your application and an external LLM. It replaces German personal
        data with placeholders before a prompt leaves your network, puts the real values back into
        the answer, and records every call in a log that shows if it was altered.
      </p>
      <div className="enter">
        <button
          type="button"
          className="primary big"
          disabled={!ready || busy}
          onClick={() => void run(api.guestLogin)}
        >
          Try the live demo
        </button>
        <p className="hint">No sign-up. You get a private sandbox, deleted after 60 minutes.</p>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <details className="signin">
        <summary>Sign in with an account</summary>
        <form onSubmit={signIn}>
          <label>
            E-mail
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <button type="submit" disabled={!ready || busy}>
            Sign in
          </button>
        </form>
      </details>
      <p className="smallprint">
        This is PII minimisation, not a guarantee: detection is measured on a benchmark and misses
        some values. The limits are listed in the project README.
      </p>
    </main>
  );
}
