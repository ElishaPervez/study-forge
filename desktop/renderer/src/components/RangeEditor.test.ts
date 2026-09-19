import { describe, expect, it } from "vitest";

import { validateRangeDraft } from "./RangeEditor";

const draft = (overrides: Partial<Parameters<typeof validateRangeDraft>[0]> = {}) => ({
  id: "row-1",
  label: "Chapter one",
  start: "1",
  end: "5",
  ...overrides,
});

describe("validateRangeDraft", () => {
  it("requires a label and one-based integer pages", () => {
    const errors = validateRangeDraft(
      draft({ label: " ", start: "0", end: "2.5" }),
      null,
    );

    expect(errors).toEqual({
      label: "Add a label for this unit.",
      start: "Use a whole page number from 1 onward.",
      end: "Use a whole page number from 1 onward.",
    });
  });

  it("rejects a range whose start comes after its end", () => {
    expect(
      validateRangeDraft(draft({ start: "8", end: "3" }), null),
    ).toEqual({ range: "Start page must be on or before end page." });
  });

  it("checks the upper bound only when page count is known", () => {
    expect(validateRangeDraft(draft({ end: "21" }), null)).toEqual({});
    expect(validateRangeDraft(draft({ end: "21" }), 20)).toEqual({
      end: "This page is past the 20-page PDF.",
    });
  });
});
