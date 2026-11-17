// npm audit with a reviewed allowlist. Fails on any high or critical advisory
// that is not listed in audit-allowlist.json, and on allowlist entries that no
// longer match anything (so stale exceptions get removed).
//
// npm audit itself has no way to ignore a single advisory; this is the
// narrowest exception possible: one advisory id, with the reason written down.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const allowlist = JSON.parse(readFileSync(new URL("../audit-allowlist.json", import.meta.url)));
const BLOCKING = new Set(["high", "critical"]);

let raw;
try {
  raw = execFileSync("npm", ["audit", "--json"], { encoding: "utf8" });
} catch (error) {
  raw = error.stdout; // npm audit exits non-zero when it finds anything
}
const report = JSON.parse(raw);

const blocking = new Map();
const seen = new Set();
for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  for (const via of vulnerability.via) {
    if (typeof via !== "object" || !BLOCKING.has(via.severity)) continue;
    const id = via.url.split("/").pop();
    if (id in allowlist) {
      seen.add(id);
    } else {
      blocking.set(id, `${via.severity}: ${via.name} ${via.range} ${via.title} (${via.url})`);
    }
  }
}

for (const id of seen) console.log(`allowed ${id}: ${allowlist[id]}`);
const stale = Object.keys(allowlist).filter((id) => !seen.has(id));
for (const id of stale) console.error(`stale allowlist entry ${id}: no longer reported, remove it`);
for (const line of blocking.values()) console.error(line);

if (blocking.size > 0 || stale.length > 0) process.exit(1);
console.log("npm audit: no high or critical advisories outside the allowlist");
