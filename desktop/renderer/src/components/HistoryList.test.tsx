import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { GuideSummary } from "../api";
import {
  HistoryList,
  historyItemOpensGuide,
  resolveSubmittedGuideName,
  sortHistoryNewestFirst,
} from "./HistoryList";

const source = {
  source_id: "source-1",
  kind: "pdf" as const,
  display_name: "course.pdf",
  files: ["course.pdf"],
  page_count: 8,
  image_count: null,
  total_bytes: 100,
  created_at: "2026-09-19T00:00:00Z",
};

function guide(overrides: Partial<GuideSummary> = {}): GuideSummary {
  return {
    guide_id: "guide-1",
    source_id: source.source_id,
    selection: { mode: "custom", start: 2, end: 4 },
    name: "Older guide",
    status: "ok",
    created_at: "2026-09-19T00:00:00Z",
    updated_at: "2026-09-19T00:00:00Z",
    error: null,
    findings: [],
    revision_count: 0,
    source,
    artifact_url: "/api/guides/guide-1/artifact.html",
    ...overrides,
  };
}

describe("HistoryList", () => {
  it("keeps duplicate source and range guides as separate newest-first entries", () => {
    const older = guide({ guide_id: "guide-1", name: "Older guide" });
    const newerDuplicate = guide({
      guide_id: "guide-2",
      name: "Newer guide",
      created_at: "2026-09-19T00:01:00Z",
      updated_at: "2026-09-19T00:02:00Z",
      artifact_url: "/api/guides/guide-2/artifact.html",
    });

    const markup = renderToStaticMarkup(
      <HistoryList
        guides={[older, newerDuplicate]}
        onOpen={() => undefined}
        onRename={() => undefined}
        onDelete={() => undefined}
        onRetry={() => undefined}
      />,
    );

    expect(markup.match(/class="history-item(?: is-openable)?(?: is-active)?"/g)).toHaveLength(2);
    expect(markup.indexOf("Newer guide")).toBeLessThan(markup.indexOf("Older guide"));
    expect(markup.match(/Pages 2–4/g)).toHaveLength(2);
  });

  it.each(["failed", "needs-attention"])("keeps Retry and Delete in a %s guide's context menu", (status) => {
    const failed = guide({
      guide_id: "failed-guide",
      name: "Failed guide",
      status,
      error: "The model did not return a guide.",
      artifact_url: null,
    });

    const markup = renderToStaticMarkup(
      <HistoryList
        guides={[failed]}
        onOpen={() => undefined}
        onRename={() => undefined}
        onDelete={() => undefined}
        onRetry={() => undefined}
      />,
    );

    expect(markup).toContain('aria-haspopup="menu"');
    expect(markup).toContain('aria-keyshortcuts="Shift+F10"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).not.toContain("is-openable");
    expect(markup).not.toContain(">Retry</button>");
    expect(markup).not.toContain(">Delete</button>");
    expect(markup).not.toContain(">Open</button>");
    expect(markup).not.toContain(">Rename</button>");
  });

  it("explains that an old multi-range record cannot be opened as a v1 guide", () => {
    const markup = renderToStaticMarkup(
      <HistoryList
        guides={[{
          kind: "legacy",
          history_id: "legacy-old-job",
          job_id: "old-job",
          name: "course.pdf",
          status: "legacy",
          message: "This older multi-range job is not compatible with Study Forge v1. Choose the source again to create a new guide.",
          created_at: "2026-09-19T00:00:00Z",
          updated_at: "2026-09-19T00:00:00Z",
        }]}
        onOpen={() => undefined}
        onRename={() => undefined}
        onDelete={() => undefined}
        onRetry={() => undefined}
      />,
    );

    expect(markup).toContain("not compatible with Study Forge v1");
    expect(markup).toContain("Choose the source again");
    expect(markup).not.toContain(">Open</button>");
    expect(markup).not.toContain(">Retry</button>");
    expect(markup).not.toContain("aria-haspopup");
  });

  it("marks a ready guide's card as openable and keeps Open out of its context menu", () => {
    const markup = renderToStaticMarkup(
      <HistoryList
        guides={[guide()]}
        onOpen={() => undefined}
        onRename={() => undefined}
        onDelete={() => undefined}
        onRetry={() => undefined}
      />,
    );

    expect(markup).toContain('class="history-item is-openable"');
    expect(markup).toContain('aria-haspopup="menu"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).not.toContain(">Open</button>");
    expect(markup).not.toContain(">Rename</button>");
    expect(markup).not.toContain(">Delete</button>");
  });

  it("only lets a ready, unlocked card open on a plain click", () => {
    expect(historyItemOpensGuide("ok", false, false)).toBe(true);
    expect(historyItemOpensGuide("ok", true, false)).toBe(false);
    expect(historyItemOpensGuide("ok", false, true)).toBe(false);
    expect(historyItemOpensGuide("failed", false, false)).toBe(false);
    expect(historyItemOpensGuide("needs-attention", false, false)).toBe(false);
    expect(historyItemOpensGuide("running", false, false)).toBe(false);
    expect(historyItemOpensGuide("legacy", false, false)).toBe(false);
  });

  it("keeps a guide with an unavailable source readable in history", () => {
    const unavailable = guide({
      status: "failed",
      source: null,
      source_error: "The stored source could not be read. Choose the source again in the Source section.",
    });

    const markup = renderToStaticMarkup(
      <HistoryList
        guides={[unavailable]}
        onOpen={() => undefined}
        onRename={() => undefined}
        onDelete={() => undefined}
        onRetry={() => undefined}
      />,
    );

    expect(markup).toContain("Source unavailable");
    expect(markup).toContain("Choose the source again");
    expect(markup).toContain('role="alert"');
    expect(markup).toContain('aria-haspopup="menu"');
    expect(markup).not.toContain("is-openable");
  });

  it("offers no context menu while the rail is busy or the guide has nothing to run", () => {
    const busyMarkup = renderToStaticMarkup(
      <HistoryList
        guides={[guide()]}
        disabled={true}
        onOpen={() => undefined}
        onRename={() => undefined}
        onDelete={() => undefined}
        onRetry={() => undefined}
      />,
    );
    const runningMarkup = renderToStaticMarkup(
      <HistoryList
        guides={[guide({ guide_id: "running-guide", status: "running", artifact_url: null })]}
        onOpen={() => undefined}
        onRename={() => undefined}
        onDelete={() => undefined}
        onRetry={() => undefined}
      />,
    );

    expect(busyMarkup).not.toContain("aria-haspopup");
    expect(busyMarkup).not.toContain("tabindex");
    expect(busyMarkup).not.toContain("is-openable");
    expect(runningMarkup).not.toContain("aria-haspopup");
    expect(runningMarkup).not.toContain("is-openable");
  });

  it("keeps the previous name when the submitted name is empty", () => {
    expect(resolveSubmittedGuideName("Current name", "   ")).toBe("Current name");
    expect(resolveSubmittedGuideName("Current name", "  New name  ")).toBe("New name");
  });

  it("sorts by the persisted update time without merging duplicate entries", () => {
    const first = guide({
      guide_id: "first",
      updated_at: "2026-09-19T00:00:00Z",
    });
    const second = guide({
      guide_id: "second",
      updated_at: "2026-09-19T00:00:01Z",
    });

    expect(sortHistoryNewestFirst([first, second]).map((item) => item.guide_id)).toEqual([
      "second",
      "first",
    ]);
  });
});
