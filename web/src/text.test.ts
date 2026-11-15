import { describe, expect, it } from "vitest";

import { entityValues, markRestored, segmentByEntities, splitPlaceholders } from "./text";

const entity = (start: number, end: number, placeholder = "<PERSON_1>") => ({
  type: "PERSON",
  start,
  end,
  placeholder,
  detector: "test",
});

describe("segmentByEntities", () => {
  it("cuts text into plain and entity segments", () => {
    const segments = segmentByEntities("Frau Schmidt schreibt.", [entity(5, 12)]);
    expect(segments.map((s) => [s.text, s.entity?.placeholder])).toEqual([
      ["Frau ", undefined],
      ["Schmidt", "<PERSON_1>"],
      [" schreibt.", undefined],
    ]);
  });

  it("uses code point offsets, as the API does", () => {
    // "👍" is one code point but two UTF-16 units.
    const segments = segmentByEntities("👍 Frau Schmidt", [entity(7, 14)]);
    expect(segments.at(-1)?.text).toBe("Schmidt");
  });

  it("handles entities at both ends and unsorted input", () => {
    const segments = segmentByEntities("Anna und Jonas", [entity(9, 14, "<PERSON_2>"), entity(0, 4)]);
    expect(segments.map((s) => s.text)).toEqual(["Anna", " und ", "Jonas"]);
  });

  it("ignores overlapping and out-of-range entities", () => {
    const segments = segmentByEntities("Anna Schmidt", [entity(0, 4), entity(2, 8), entity(10, 99)]);
    expect(segments.map((s) => s.text)).toEqual(["Anna", " Schmidt"]);
  });
});

describe("splitPlaceholders", () => {
  it("separates placeholder tokens from text", () => {
    expect(splitPlaceholders("An <PERSON_1>, IBAN <IBAN_12>.")).toEqual([
      { text: "An ", isPlaceholder: false },
      { text: "<PERSON_1>", isPlaceholder: true },
      { text: ", IBAN ", isPlaceholder: false },
      { text: "<IBAN_12>", isPlaceholder: true },
      { text: ".", isPlaceholder: false },
    ]);
  });

  it("leaves ordinary angle brackets alone", () => {
    expect(splitPlaceholders("a < b und <b>fett</b>")).toEqual([
      { text: "a < b und <b>fett</b>", isPlaceholder: false },
    ]);
  });
});

describe("markRestored", () => {
  it("marks values that replaced a placeholder, longest first", () => {
    const text = "Anna Schmidt und Anna";
    const values = entityValues(text, [entity(0, 12), entity(17, 21, "<PERSON_2>")]);
    expect(markRestored("Hallo Anna Schmidt, hallo Anna!", values)).toEqual([
      { text: "Hallo " },
      { text: "Anna Schmidt", restoredFrom: "<PERSON_1>" },
      { text: ", hallo " },
      { text: "Anna", restoredFrom: "<PERSON_2>" },
      { text: "!" },
    ]);
  });

  it("returns the answer unchanged when nothing was restored", () => {
    expect(markRestored("Kein Treffer.", new Map())).toEqual([{ text: "Kein Treffer." }]);
  });
});
