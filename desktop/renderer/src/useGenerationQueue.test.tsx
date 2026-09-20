// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import type { Api, GuideView, QueueRow, QueueSummary } from "./api";
import { useGenerationQueue, type GenerationQueue } from "./useGenerationQueue";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function row(overrides: Partial<QueueRow> = {}): QueueRow {
  return {
    receipt: "a".repeat(32),
    guide_id: "guide-1",
    order: 1,
    kind: "create",
    state: "running",
    created_at: "2026-09-20T00:00:00Z",
    started_at: "2026-09-20T00:00:01Z",
    finished_at: null,
    retry_available: false,
    error: null,
    guide_name: "Course",
    activity: "writing the guide",
    attempt: 1,
    lines: 12,
    characters: 340,
    last_output_at: "2026-09-20T00:00:02Z",
    ...overrides,
  };
}

function snapshot(overrides: Partial<QueueSummary> = {}): QueueSummary {
  return {
    service_start: "service-1",
    change_number: 1,
    accepting: true,
    closing: false,
    operations: [],
    ...overrides,
  };
}

function acceptedGuide(overrides: Partial<GuideView> = {}): GuideView {
  return {
    guide_id: "guide-new",
    source_id: "source-1",
    selection: { mode: "all" },
    name: "Course",
    status: "pending",
    created_at: "2026-09-20T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    error: null,
    findings: [],
    revision_count: 0,
    artifact_url: null,
    ...overrides,
  };
}

interface FakeApi extends Api {
  getQueue: ReturnType<typeof vi.fn>;
  getOperation: ReturnType<typeof vi.fn>;
  createGuide: ReturnType<typeof vi.fn>;
  retryOperation: ReturnType<typeof vi.fn>;
}

function fakeApi(overrides: Partial<Api> = {}): FakeApi {
  return {
    getQueue: vi.fn(async () => snapshot()),
    getOperation: vi.fn(async () => row()),
    createGuide: vi.fn(async () => acceptedGuide()),
    generateGuide: vi.fn(async () => acceptedGuide()),
    retryGuide: vi.fn(async () => acceptedGuide()),
    retryOperation: vi.fn(async () => acceptedGuide()),
    reviseGuide: vi.fn(async () => acceptedGuide()),
    ...overrides,
  } as unknown as FakeApi;
}

let container: HTMLDivElement;
let root: Root;
let latest: GenerationQueue;

function Probe({ api, onGuideSettled }: { api: Api; onGuideSettled?: (id: string) => void }) {
  latest = useGenerationQueue({ api, onGuideSettled });
  return null;
}

async function mount(api: Api, onGuideSettled?: (id: string) => void) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<Probe api={api} onGuideSettled={onGuideSettled} />);
  });
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function settle(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container?.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useGenerationQueue", () => {
  it("loads the current queue and reports which guides are busy", async () => {
    const api = fakeApi({
      getQueue: vi.fn(async () =>
        snapshot({
          change_number: 3,
          operations: [
            row({ receipt: "a".repeat(32), guide_id: "guide-1", state: "running" }),
            row({
              receipt: "b".repeat(32),
              guide_id: "guide-2",
              order: 2,
              state: "waiting",
              guide_name: "Second",
            }),
          ],
        }),
      ),
    });

    await mount(api);

    expect(api.getQueue).toHaveBeenCalledTimes(1);
    expect(latest.rows.map((item) => item.guide_id)).toEqual(["guide-1", "guide-2"]);
    expect([...latest.busyGuides]).toEqual(["guide-1", "guide-2"]);
    expect(latest.connected).toBe(true);
    expect(latest.error).toBeNull();
  });

  it("refreshes every 500 ms while work is moving and every 2 s when idle", async () => {
    const responses = [
      snapshot({ change_number: 1, operations: [row({ state: "running" })] }),
      snapshot({ change_number: 2, operations: [row({ state: "completed" })] }),
      snapshot({ change_number: 3 }),
    ];
    let index = 0;
    const api = fakeApi({
      getQueue: vi.fn(async () => responses[Math.min(index++, responses.length - 1)]),
    });

    await mount(api);
    expect(api.getQueue).toHaveBeenCalledTimes(1);

    await settle(499);
    expect(api.getQueue).toHaveBeenCalledTimes(1);
    await settle(1);
    expect(api.getQueue).toHaveBeenCalledTimes(2);

    await settle(1999);
    expect(api.getQueue).toHaveBeenCalledTimes(2);
    await settle(1);
    expect(api.getQueue).toHaveBeenCalledTimes(3);
  });

  it("never keeps two refreshes in flight at once", async () => {
    let release: (value: QueueSummary) => void = () => undefined;
    const api = fakeApi({
      getQueue: vi.fn(
        () =>
          new Promise<QueueSummary>((resolve) => {
            release = resolve;
          }),
      ),
    });

    await mount(api);
    await settle(5000);
    expect(api.getQueue).toHaveBeenCalledTimes(1);

    await act(async () => {
      release(snapshot({ change_number: 2, operations: [row({ state: "running" })] }));
      await Promise.resolve();
    });
    await settle(500);
    expect(api.getQueue).toHaveBeenCalledTimes(2);
  });

  it("ignores a snapshot older than the one already shown", async () => {
    const newer = snapshot({ change_number: 5, operations: [row({ state: "running" })] });
    const older = snapshot({ change_number: 2, operations: [] });
    let calls = 0;
    const api = fakeApi({
      getQueue: vi.fn(async () => {
        calls += 1;
        return calls === 1 ? newer : older;
      }),
    });

    await mount(api);
    await settle(500);

    expect(latest.rows).toHaveLength(1);
    expect(latest.busyGuides.has("guide-1")).toBe(true);
  });

  it("starts again when the service has been replaced", async () => {
    const first = snapshot({
      service_start: "service-1",
      change_number: 9,
      operations: [row({ state: "running" })],
    });
    const completed = snapshot({
      service_start: "service-1",
      change_number: 10,
      operations: [row({ state: "completed" })],
    });
    const restarted = snapshot({
      service_start: "service-2",
      change_number: 1,
      operations: [
        row({
          receipt: "c".repeat(32),
          guide_id: "guide-9",
          state: "interrupted",
          error: "The app stopped before this request finished. Retry to continue.",
        }),
      ],
    });
    const responses = [first, completed, restarted];
    let index = 0;
    const api = fakeApi({
      getQueue: vi.fn(async () => responses[Math.min(index++, responses.length - 1)]),
    });

    await mount(api);
    await settle(500);
    expect(latest.finished.map((notice) => notice.receipt)).toEqual(["a".repeat(32)]);

    await settle(2000);

    expect(latest.finished).toEqual([]);
    expect(latest.rows.map((item) => item.guide_id)).toEqual(["guide-9"]);
    expect(latest.busyGuides.size).toBe(0);
  });

  it("stops refreshing after unmount", async () => {
    const api = fakeApi();
    await mount(api);
    expect(api.getQueue).toHaveBeenCalledTimes(1);

    act(() => {
      root.unmount();
    });
    await settle(10_000);

    expect(api.getQueue).toHaveBeenCalledTimes(1);
  });

  it("locks an accepted guide before the next refresh", async () => {
    const api = fakeApi({
      getQueue: vi.fn(async () => snapshot({ change_number: 4 })),
      createGuide: vi.fn(async () =>
        acceptedGuide({
          operation: row({ receipt: "f".repeat(32), guide_id: "guide-new", state: "waiting" }),
        }),
      ),
    });
    await mount(api);

    await act(async () => {
      await latest.submitGuide("source-1", { mode: "all" });
    });

    expect(latest.busyGuides.has("guide-new")).toBe(true);
    expect(latest.submitting).toBe(false);

    // An older reply that has not heard about the acceptance cannot unlock it.
    await settle(500);
    expect(latest.busyGuides.has("guide-new")).toBe(true);
  });

  it("keeps one receipt when the acceptance reply is lost", async () => {
    const api = fakeApi({
      createGuide: vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
      getOperation: vi.fn(async () => {
        throw new ApiError("unknown request", 404);
      }),
    });
    await mount(api);

    await act(async () => {
      await expect(latest.submitGuide("source-1", { mode: "all" })).rejects.toThrow(
        "Failed to fetch",
      );
    });
    await act(async () => {
      await expect(latest.submitGuide("source-1", { mode: "all" })).rejects.toThrow(
        "Failed to fetch",
      );
    });

    const first = api.createGuide.mock.calls[0][2] as string;
    const second = api.createGuide.mock.calls[1][2] as string;
    expect(first).toBe(second);
    expect(api.createGuide).toHaveBeenCalledTimes(2);
  });

  it("finds an accepted request again instead of creating a second guide", async () => {
    const recorded = row({ receipt: "9".repeat(32), guide_id: "guide-lost", state: "waiting" });
    const api = fakeApi({
      createGuide: vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
      getOperation: vi.fn(async () => recorded),
    });
    await mount(api);

    let outcome;
    await act(async () => {
      outcome = await latest.submitGuide("source-1", { mode: "all" });
    });

    expect(outcome).toMatchObject({ guide_id: "guide-lost", guide: null });
    expect(api.createGuide).toHaveBeenCalledTimes(1);
    expect(latest.busyGuides.has("guide-lost")).toBe(true);

    // The receipt is resolved, so a deliberate new submission uses a fresh one.
    await act(async () => {
      await latest.submitGuide("source-1", { mode: "all" });
    });
    expect(api.createGuide).toHaveBeenCalledTimes(2);
    expect(api.createGuide.mock.calls[0][2]).not.toBe(api.createGuide.mock.calls[1][2]);
  });

  it("keeps the last known rows and reports a lost connection", async () => {
    let calls = 0;
    const api = fakeApi({
      getQueue: vi.fn(async () => {
        calls += 1;
        if (calls === 1) {
          return snapshot({ change_number: 2, operations: [row({ state: "running" })] });
        }
        throw new TypeError("Failed to fetch");
      }),
    });

    await mount(api);
    await settle(500);

    expect(latest.connected).toBe(false);
    expect(latest.error).toBe("Failed to fetch");
    expect(latest.rows).toHaveLength(1);
    expect(latest.busyGuides.has("guide-1")).toBe(true);
  });

  it("reports a guide once when its request stops and can dismiss the notice", async () => {
    const settled = vi.fn();
    const responses = [
      snapshot({ change_number: 1, operations: [row({ state: "running" })] }),
      snapshot({ change_number: 2, operations: [row({ state: "failed", error: "no guide" })] }),
      snapshot({ change_number: 3, operations: [row({ state: "failed", error: "no guide" })] }),
    ];
    let index = 0;
    const api = fakeApi({
      getQueue: vi.fn(async () => responses[Math.min(index++, responses.length - 1)]),
    });

    await mount(api, settled);
    await settle(500);
    await settle(2000);

    expect(settled).toHaveBeenCalledTimes(1);
    expect(settled).toHaveBeenCalledWith("guide-1");
    expect(latest.rows).toHaveLength(1);

    act(() => {
      latest.dismiss("a".repeat(32));
    });
    expect(latest.rows).toHaveLength(0);
  });

  it("does not send a request whose submission handshake was refused", async () => {
    const beginSubmission = vi.fn(async () => null);
    const endSubmission = vi.fn(async () => undefined);
    (window as unknown as { studyForge?: unknown }).studyForge = {
      beginSubmission,
      endSubmission,
    };
    const api = fakeApi();
    await mount(api);

    await expect(
      act(async () => {
        await latest.submitGuide("source-1", { mode: "all" });
      }),
    ).rejects.toThrow(/is closing/);

    expect(beginSubmission).toHaveBeenCalledTimes(1);
    expect(api.createGuide).not.toHaveBeenCalled();
    expect(endSubmission).not.toHaveBeenCalled();

    delete (window as unknown as { studyForge?: unknown }).studyForge;
  });

  it("ends the submission handshake once the request is accepted", async () => {
    const beginSubmission = vi.fn(async () => 11);
    const endSubmission = vi.fn(async () => undefined);
    (window as unknown as { studyForge?: unknown }).studyForge = {
      beginSubmission,
      endSubmission,
    };
    const api = fakeApi();
    await mount(api);

    await act(async () => {
      await latest.submitGuide("source-1", { mode: "all" });
    });

    expect(api.createGuide).toHaveBeenCalledTimes(1);
    expect(beginSubmission).toHaveBeenCalledTimes(1);
    expect(endSubmission).toHaveBeenCalledWith(11);

    delete (window as unknown as { studyForge?: unknown }).studyForge;
  });
});
