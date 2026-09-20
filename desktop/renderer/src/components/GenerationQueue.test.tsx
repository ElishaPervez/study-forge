// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { QueueRow } from "../api";
import {
  GenerationQueue,
  orderQueueRows,
  progressDetail,
  queueSummaryText,
  type GenerationQueueProps,
} from "./GenerationQueue";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const NOW = Date.parse("2026-09-20T12:00:00Z");

function row(overrides: Partial<QueueRow> = {}): QueueRow {
  return {
    receipt: "a".repeat(32),
    guide_id: "guide-1",
    order: 1,
    kind: "create",
    state: "running",
    created_at: "2026-09-20T11:58:00Z",
    started_at: "2026-09-20T11:58:01Z",
    finished_at: null,
    retry_available: false,
    error: null,
    guide_name: "Course",
    activity: "writing",
    attempt: 1,
    lines: 0,
    characters: 0,
    last_output_at: "2026-09-20T11:59:59Z",
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: Root;

const actions = {
  onOpenGuide: vi.fn(),
  onRetry: vi.fn(),
  onDismiss: vi.fn(),
};

async function render(props: Partial<GenerationQueueProps> = {}) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await draw(props);
}

async function draw(props: Partial<GenerationQueueProps> = {}) {
  const full: GenerationQueueProps = {
    rows: [],
    connected: true,
    now: () => NOW,
    ...actions,
    ...props,
  };
  await act(async () => {
    root.render(<GenerationQueue {...full} />);
  });
}

function text(): string {
  return container.textContent ?? "";
}

function findButton(label: string): HTMLButtonElement {
  const match = [...container.querySelectorAll("button")].find((button) =>
    button.textContent?.trim().startsWith(label),
  );
  if (!match) throw new Error(`no ${label} button in: ${text()}`);
  return match as HTMLButtonElement;
}

function toggle(): HTMLButtonElement {
  const match = container.querySelector<HTMLButtonElement>(".generation-queue-toggle");
  if (!match) throw new Error("no queue toggle");
  return match;
}

function click(button: HTMLButtonElement) {
  act(() => {
    button.click();
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container?.remove();
});

describe("GenerationQueue", () => {
  it("shows nothing while the queue is empty", async () => {
    await render({ rows: [] });

    expect(container.innerHTML).toBe("");
  });

  it("counts running and waiting work without claiming finished guides are running", async () => {
    await render({
      rows: [
        row({ receipt: "a".repeat(32), state: "running" }),
        row({
          receipt: "b".repeat(32),
          guide_id: "guide-2",
          order: 2,
          state: "waiting",
          activity: "waiting",
        }),
        row({
          receipt: "c".repeat(32),
          guide_id: "guide-3",
          order: 3,
          state: "waiting",
          activity: "waiting",
        }),
        row({
          receipt: "d".repeat(32),
          guide_id: "guide-4",
          order: 4,
          state: "completed",
          finished_at: "2026-09-20T11:59:30Z",
        }),
      ],
    });

    expect(text()).toContain("1 generating · 2 waiting");

    await draw({
      rows: [
        row({
          receipt: "d".repeat(32),
          guide_id: "guide-4",
          order: 4,
          state: "completed",
          finished_at: "2026-09-20T11:59:30Z",
        }),
      ],
    });

    expect(text()).toContain("1 guide ready");
    expect(text()).not.toContain("generating");
  });

  it("expands for new work and keeps the collapsed state while rows update", async () => {
    await render({ rows: [row()] });
    expect(toggle().getAttribute("aria-expanded")).toBe("true");

    click(toggle());
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".generation-queue-list")).toBeNull();

    await draw({ rows: [row({ lines: 500, characters: 24_300 })] });

    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".generation-queue-list")).toBeNull();

    click(toggle());
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector(".generation-queue-list")).not.toBeNull();
  });

  it("opens a waiting guide and retries a failed request from real buttons", async () => {
    const failed = row({
      receipt: "b".repeat(32),
      guide_id: "guide-2",
      order: 2,
      state: "failed",
      activity: "checking",
      error: "The request finished without a verified guide.",
      retry_available: true,
    });
    await render({ rows: [failed] });

    click(findButton("Open"));
    click(findButton("Retry"));

    expect(actions.onOpenGuide).toHaveBeenCalledWith("guide-2");
    expect(actions.onRetry).toHaveBeenCalledWith(failed);
    expect(text()).toContain("The request finished without a verified guide.");
  });

  it("dismisses a finished notice without touching the saved guide", async () => {
    const completed = row({
      receipt: "c".repeat(32),
      state: "completed",
      finished_at: "2026-09-20T11:59:30Z",
    });
    await render({ rows: [completed] });

    click(findButton("Dismiss"));

    expect(actions.onDismiss).toHaveBeenCalledWith("c".repeat(32));
  });

  it("describes waiting, writing, checking, correcting and quiet work truthfully", async () => {
    const waiting = row({ receipt: "a".repeat(32), state: "waiting", activity: "waiting" });
    const writing = row({ receipt: "b".repeat(32), order: 2, activity: "writing", lines: 500, characters: 24_300 });
    const checking = row({
      receipt: "c".repeat(32),
      order: 3,
      activity: "checking",
      lines: 512,
      characters: 25_000,
    });
    const correcting = row({
      receipt: "d".repeat(32),
      order: 4,
      activity: "correcting",
      attempt: 2,
      lines: 8,
      characters: 300,
    });

    await render({ rows: [waiting, writing, checking, correcting] });

    expect(progressDetail(waiting, NOW)).toBe("Waiting to start");
    expect(text()).toContain("Writing the guide · 500 lines · 24300 characters");
    expect(text()).toContain("Checking the guide · 512 lines");
    expect(text()).toContain("Correcting the guide (attempt 2)");
    expect(text()).not.toContain("%");
    expect(text()).not.toMatch(/remaining|estimated|time left|progress:/i);
  });

  it("measures how long writing has been quiet instead of guessing at progress", async () => {
    const quiet = row({
      activity: "writing",
      lines: 40,
      characters: 900,
      last_output_at: new Date(NOW - 25_000).toISOString(),
    });
    const nothingYet = row({
      receipt: "b".repeat(32),
      order: 2,
      activity: "writing",
      lines: 0,
      characters: 0,
      last_output_at: null,
    });

    await render({ rows: [quiet, nothingYet] });

    expect(progressDetail(quiet, NOW)).toContain("no new lines for 25s");
    expect(text()).toContain("no new lines for 25s");
    expect(text()).toContain("Writing the guide");
  });

  it("reports a lost connection without unlocking the busy guides", async () => {
    const busy = row({ state: "running" });
    await render({
      rows: [busy],
      connected: false,
      error: "Failed to fetch",
    });

    expect(queueSummaryText([busy], false)).toBe("Not connected · work may still be running");
    expect(text()).toContain("Failed to fetch");
    expect(text()).toContain("stay locked");
    expect(container.querySelector(".generation-queue-row.is-running")).not.toBeNull();
  });

  it("announces activity changes but not every new line", async () => {
    await render({ rows: [row({ activity: "writing", lines: 12, characters: 340 })] });
    const first = container.querySelector('[role="status"]')?.textContent;

    await draw({ rows: [row({ activity: "writing", lines: 40, characters: 1_200 })] });
    const second = container.querySelector('[role="status"]')?.textContent;

    await draw({ rows: [row({ activity: "checking", lines: 40, characters: 1_200 })] });
    const third = container.querySelector('[role="status"]')?.textContent;

    expect(first).toContain("Writing the guide");
    expect(first).not.toContain("12");
    expect(second).toBe(first);
    expect(third).toContain("Checking the guide");
  });

  it("keeps active work first, waiting work in accepted order and notices last", () => {
    const ordered = orderQueueRows([
      row({ receipt: "d".repeat(32), order: 5, state: "completed" }),
      row({ receipt: "b".repeat(32), order: 3, state: "waiting" }),
      row({ receipt: "a".repeat(32), order: 1, state: "running" }),
      row({ receipt: "c".repeat(32), order: 4, state: "failed", retry_available: true }),
      row({ receipt: "e".repeat(32), order: 2, state: "waiting" }),
    ]);

    expect(ordered.map((item) => item.receipt)).toEqual([
      "a".repeat(32),
      "e".repeat(32),
      "b".repeat(32),
      "c".repeat(32),
      "d".repeat(32),
    ]);
  });
});
