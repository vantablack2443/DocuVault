import { api, type RequestRow, type UsageDay } from "../api";
import { formatDay, formatTime, useLoad } from "../hooks";
import { typeLabel } from "../text";

export function Usage() {
  const usage = useLoad(() => api.usage(14));
  const requests = useLoad(() => api.requests(25));
  const error = usage.error ?? requests.error;

  return (
    <section>
      <div className="heading-row">
        <h1>Usage</h1>
        <button
          type="button"
          className="quiet"
          onClick={() => {
            usage.reload();
            requests.reload();
          }}
        >
          Refresh
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {usage.data && <Chart days={usage.data} />}
      {requests.data && <Requests rows={requests.data} />}
    </section>
  );
}

function Chart({ days }: { days: UsageDay[] }) {
  if (days.length === 0) {
    return (
      <p className="empty">
        No calls in the last 14 days. Send a message in the playground to see it counted here.
      </p>
    );
  }
  const total = (pick: (day: UsageDay) => number) => days.reduce((sum, day) => sum + pick(day), 0);
  const requests = total((day) => day.requests);
  const cached = total((day) => day.cache_hits);
  const peak = Math.max(...days.map((day) => day.requests), 1);
  const width = 640;
  const height = 180;
  const slot = width / days.length;
  const bar = Math.min(34, slot * 0.6);
  const scale = (value: number) => (value / peak) * (height - 24);

  return (
    <figure className="chart">
      <figcaption>
        <strong>{requests}</strong> calls in the last 14 days, <strong>{cached}</strong> answered
        from the cache, <strong>{total((day) => day.redactions)}</strong> personal values replaced,{" "}
        <strong>{total((day) => day.prompt_tokens + day.completion_tokens)}</strong> tokens.
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${height + 22}`}
        role="img"
        aria-label={`Calls per day. Highest day: ${peak} calls.`}
      >
        <line x1="0" x2={width} y1={height} y2={height} className="axis" />
        {days.map((day, index) => {
          const x = index * slot + (slot - bar) / 2;
          const full = scale(day.requests);
          const hits = scale(day.cache_hits);
          return (
            <g key={day.day}>
              <title>
                {formatDay(day.day)}: {day.requests} calls, {day.cache_hits} from cache
              </title>
              <rect x={x} y={height - full} width={bar} height={full - hits} className="bar-model" />
              <rect x={x} y={height - hits} width={bar} height={hits} className="bar-cache" />
              <text x={x + bar / 2} y={height - full - 6} textAnchor="middle" className="value">
                {day.requests}
              </text>
              <text x={x + bar / 2} y={height + 16} textAnchor="middle" className="tick">
                {formatDay(day.day)}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="legend">
        <span className="key key-model" /> sent to a model
        <span className="key key-cache" /> answered from cache
      </p>
    </figure>
  );
}

function Requests({ rows }: { rows: RequestRow[] }) {
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="scroll">
      <table>
        <caption>Latest calls</caption>
        <thead>
          <tr>
            <th>Time</th>
            <th>Answered by</th>
            <th>Result</th>
            <th className="num">Duration</th>
            <th className="num">Tokens</th>
            <th>Replaced</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{formatTime(row.created_at)}</td>
              <td>{row.cache_hit ? "cache" : row.provider}</td>
              <td className={row.status === "ok" ? undefined : "bad"}>
                {row.status === "ok" ? "OK" : row.status}
                {row.stream && ", streamed"}
              </td>
              <td className="num">{row.latency_ms} ms</td>
              <td className="num">{row.prompt_tokens + row.completion_tokens}</td>
              <td>
                {Object.entries(row.redaction_counts)
                  .map(([type, count]) => `${count} ${typeLabel(type)}`)
                  .join(", ") || "nothing"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
