// Pure text helpers for the playground. Kept free of React so they can be tested.

export interface Entity {
  type: string;
  start: number;
  end: number;
  placeholder: string;
  detector: string;
}

export interface Segment {
  text: string;
  entity?: Entity;
}

/**
 * Cut `text` into plain and entity segments.
 *
 * The API counts offsets in Unicode code points (Python strings); JavaScript
 * strings count UTF-16 units. Splitting into code points first keeps the
 * highlights aligned when the text contains emoji or other astral characters.
 */
export function segmentByEntities(text: string, entities: Entity[]): Segment[] {
  const points = Array.from(text);
  const ordered = [...entities].sort((a, b) => a.start - b.start);
  const segments: Segment[] = [];
  let cursor = 0;
  for (const entity of ordered) {
    if (entity.start < cursor || entity.end > points.length || entity.end <= entity.start) {
      continue; // overlapping or out of range: show as plain text rather than guess
    }
    if (entity.start > cursor) {
      segments.push({ text: points.slice(cursor, entity.start).join("") });
    }
    segments.push({ text: points.slice(entity.start, entity.end).join(""), entity });
    cursor = entity.end;
  }
  if (cursor < points.length) {
    segments.push({ text: points.slice(cursor).join("") });
  }
  return segments;
}

const PLACEHOLDER = /(<[A-Z_]+_\d+>)/;

export interface PlaceholderPart {
  text: string;
  isPlaceholder: boolean;
}

/** Split redacted text so that `<PERSON_1>` style tokens can be drawn as bars. */
export function splitPlaceholders(text: string): PlaceholderPart[] {
  return text
    .split(PLACEHOLDER)
    .filter((part) => part !== "")
    .map((part) => ({ text: part, isPlaceholder: /^<[A-Z_]+_\d+>$/.test(part) }));
}

export interface RestoredPart {
  text: string;
  restoredFrom?: string;
}

/**
 * Mark the values in the final answer that were put back in place of a
 * placeholder. `values` maps original value to its placeholder.
 */
export function markRestored(answer: string, values: Map<string, string>): RestoredPart[] {
  const needles = [...values.keys()].filter((v) => v !== "").sort((a, b) => b.length - a.length);
  const parts: RestoredPart[] = [];
  let plain = "";
  let index = 0;
  while (index < answer.length) {
    const hit = needles.find((needle) => answer.startsWith(needle, index));
    if (hit === undefined) {
      plain += answer[index];
      index += 1;
      continue;
    }
    if (plain) {
      parts.push({ text: plain });
      plain = "";
    }
    parts.push({ text: hit, restoredFrom: values.get(hit) });
    index += hit.length;
  }
  if (plain) {
    parts.push({ text: plain });
  }
  return parts;
}

export function entityValues(text: string, entities: Entity[]): Map<string, string> {
  const values = new Map<string, string>();
  for (const segment of segmentByEntities(text, entities)) {
    if (segment.entity) {
      values.set(segment.text, segment.entity.placeholder);
    }
  }
  return values;
}

const TYPE_LABELS: Record<string, string> = {
  IBAN: "IBAN",
  STEUER_ID: "Tax ID",
  EMAIL: "E-mail",
  PHONE: "Phone",
  ADDRESS: "Street address",
  POSTAL_CODE: "Postal code",
  DATE_OF_BIRTH: "Date of birth",
  PERSON: "Person",
  LOCATION: "Place",
};

export const PII_TYPES = Object.keys(TYPE_LABELS);

export function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type;
}
