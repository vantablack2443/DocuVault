import { type FormEvent, useState } from "react";

import { api, describeError, type PlaygroundResult } from "../api";
import { entityValues, markRestored, segmentByEntities, splitPlaceholders, typeLabel } from "../text";

// Invented people and data; the IBAN is the public example number.
const SAMPLES = [
  {
    label: "Refund request",
    text: "Frau Schmidt möchte eine Erstattung auf DE89 3704 0044 0532 0130 00. Antwort an a.schmidt@example.de.",
  },
  {
    label: "Call note",
    text: "Bitte fasse zusammen: Herr Jonas Weber, geboren am 12.03.1985, wohnhaft in der Lindenstraße 14, 66111 Saarbrücken, hat unter 0681 123456 angerufen und bittet um Rückruf.",
  },
  {
    label: "Appointment",
    text: "Schreibe eine freundliche Absage an Frau Dr. Petra Hoffmann (petra.hoffmann@example.org) für den Termin in München.",
  },
];

const MAX_LENGTH = 5000;

export function Playground() {
  const [message, setMessage] = useState(SAMPLES[0]!.text);
  const [result, setResult] = useState<PlaygroundResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.playground(message));
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Playground</h1>
      <p className="intro">
        Write a message with personal data in it and send it through the gateway. You will see what
        was found, what the model received, and what comes back to you.
      </p>
      <form className="compose" onSubmit={submit}>
        <div className="samples">
          <span id="samples-label">Start from an example</span>
          {SAMPLES.map((sample) => (
            <button
              key={sample.label}
              type="button"
              className="quiet"
              aria-describedby="samples-label"
              onClick={() => setMessage(sample.text)}
            >
              {sample.label}
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor="message">
          Message
        </label>
        <textarea
          id="message"
          rows={4}
          maxLength={MAX_LENGTH}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
        />
        <div className="compose-foot">
          <button type="submit" className="primary" disabled={busy || message.trim() === ""}>
            {busy ? "Sending" : "Send through gateway"}
          </button>
          <span className="hint">
            {message.length} of {MAX_LENGTH} characters
          </span>
        </div>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {result && <Steps result={result} />}
    </section>
  );
}

function Steps({ result }: { result: PlaygroundResult }) {
  const segments = segmentByEntities(result.original, result.entities);
  const restored = markRestored(
    result.final_answer,
    entityValues(result.original, result.entities),
  );
  return (
    <div aria-live="polite">
      <ol className="steps">
        <li>
          <h2>You wrote</h2>
          <p className="doc">
            {segments.map((segment, index) =>
              segment.entity ? (
                <mark key={index} title={`${typeLabel(segment.entity.type)}, found by ${segment.entity.detector}`}>
                  {segment.text}
                </mark>
              ) : (
                segment.text
              ),
            )}
          </p>
          <p className="hint">
            {result.entities.length === 0
              ? "Nothing was recognised as personal data."
              : `${result.entities.length} marked ${result.entities.length === 1 ? "value was" : "values were"} recognised as personal data.`}
          </p>
        </li>
        <li>
          <h2>The model received</h2>
          <p className="doc">
            <Redacted text={result.sent_upstream} />
          </p>
          <p className="hint">This is the only version that left the gateway.</p>
        </li>
        <li>
          <h2>You get back</h2>
          <p className="doc">
            {restored.map((part, index) =>
              part.restoredFrom ? (
                <span key={index} className="restored" title={`Put back in place of ${part.restoredFrom}`}>
                  {part.text}
                </span>
              ) : (
                part.text
              ),
            )}
          </p>
          <p className="hint">Underlined values were put back by the gateway.</p>
        </li>
      </ol>
      <p className="meta">
        {result.cache_hit
          ? "Answered from the cache, so no model was called."
          : result.provider === "replay"
            ? "Answered from a recording: no live model is configured on this server."
            : `Answered by ${result.provider}.`}{" "}
        The model's raw answer was: <Redacted text={result.upstream_answer} />
      </p>
      {result.entities.length > 0 && (
        <div className="scroll">
          <table>
            <caption>What was found</caption>
            <thead>
              <tr>
                <th>Value</th>
                <th>Kind</th>
                <th>Placeholder</th>
                <th>Found by</th>
              </tr>
            </thead>
            <tbody>
              {segments.map(
                (segment, index) =>
                  segment.entity && (
                    <tr key={index}>
                      <td>{segment.text}</td>
                      <td>{typeLabel(segment.entity.type)}</td>
                      <td className="code">{segment.entity.placeholder}</td>
                      <td>{segment.entity.detector}</td>
                    </tr>
                  ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Redacted({ text }: { text: string }) {
  return (
    <>
      {splitPlaceholders(text).map((part, index) =>
        part.isPlaceholder ? (
          <span key={index} className="bar">
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </>
  );
}
