import { afterEach, describe, expect, it, vi } from "vitest";

import { TRACE_PREFIX, traceElapsedMs, traceLog, traceNow } from "./traceLog";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("traceLog", () => {
  it("prefixes entries so one click's timeline can be filtered", () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});

    traceLog("forge.clicked", { source_id: "abc" });

    expect(info).toHaveBeenCalledTimes(1);
    expect(info.mock.calls[0][0]).toBe(`${TRACE_PREFIX} forge.clicked`);
    expect(info.mock.calls[0][1]).toEqual({ source_id: "abc" });
  });

  it("carries the receipt a backend trace is named after", () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});

    traceLog("submit.accepted", { receipt: "a".repeat(32), duration_ms: 12.5 });

    expect(info.mock.calls[0][1]).toMatchObject({ receipt: "a".repeat(32), duration_ms: 12.5 });
  });

  it("never throws when the console refuses output", () => {
    vi.spyOn(console, "info").mockImplementation(() => {
      throw new Error("no console here");
    });

    expect(() => traceLog("submit.accepted")).not.toThrow();
  });

  it("measures elapsed time from a start point", () => {
    vi.spyOn(performance, "now").mockReturnValueOnce(100).mockReturnValueOnce(112.34);

    expect(traceElapsedMs(traceNow())).toBeCloseTo(12.3, 1);
  });
});
