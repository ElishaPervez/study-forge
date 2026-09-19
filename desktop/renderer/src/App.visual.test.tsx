import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import * as AppModule from "./App";
import {
  createHistoryLoadGuard,
  displayedSourceRecoveryAfterGuideDeletion,
  canExportGuide,
  canForgeStudyGuide,
  exportFeedbackContextKey,
  exportFeedbackAfterContextChange,
  exportFeedbackMessage,
  exportFilename,
  exportGuideArtifact,
  ExportAction,
  mergeGuideResponse,
  replaceGuideInHistory,
  sourcePathsForForge,
  sourceIdentityChanged,
  SourceRemoveAction,
  ViewerTabs,
  sourceControlsLocked,
  StartupErrorPanel,
  synchronizeImageSource,
  isSourceReadError,
  sourceRecoveryMessageForGuide,
  sourceErrorAfterGuideAction,
  sourceErrorForGuideResponse,
  workStateLabel,
} from "./App";
import type { GuideSummary, GuideView, SourceView } from "./api";
import type { ImageFile } from "./components/ImageGroupEditor";
import { artifactFetchOptions, GuideCard } from "./components/GuideCard";
import { selectionRectInViewer as selectionRectInGuideViewer } from "./components/GuideCard";
import { PdfRangeSelector } from "./components/PdfRangeSelector";
import { RevisionPopup } from "./components/RevisionPopup";
import { SourceIntake, draftFromStoredSource, type SourceDraft } from "./components/SourceIntake";

const currentImageFiles: ImageFile[] = [
  { path: "C:/notes/first.png", name: "first.png" },
  { path: "C:/notes/second.png", name: "second.png" },
];

const currentImageSource: SourceDraft = {
  kind: "images",
  sourceId: "source-1",
  displayName: "first.png",
  metadata: { imageCount: 2, totalBytes: 42 },
  imageFiles: currentImageFiles,
  pageCount: null,
  paths: currentImageFiles.map((file) => file.path),
};

function exportGuide(overrides: Partial<GuideView> = {}): GuideView {
  return {
    guide_id: "guide-export",
    source_id: "source-1",
    selection: { mode: "custom", start: 2, end: 3 },
    name: "Cell Division",
    status: "ok",
    created_at: "2026-09-19T00:00:00Z",
    updated_at: "2026-09-19T00:00:00Z",
    error: null,
    findings: [],
    revision_count: 1,
    artifact_url: "/api/guides/guide-export/artifact.html",
    ...overrides,
  };
}

const reorderedImageSource: SourceView = {
  source_id: "source-2",
  kind: "images",
  display_name: "second.png",
  files: ["second.png", "first.png"],
  page_count: null,
  image_count: 2,
  total_bytes: 42,
  created_at: "2026-09-19T00:00:00Z",
};

describe("Study Forge visual availability markers", () => {
  it("keeps startup failure actionable and exposes a retry control", () => {
    const markup = renderToStaticMarkup(
      <StartupErrorPanel error="The local service did not start." onRetry={() => undefined} />,
    );

    expect(markup).toContain('role="alert"');
    expect(markup).toContain("The local service did not start.");
    expect(markup).toContain(">Try again</button>");
  });

  it("uses short live progress messages for generation and revision work", () => {
    expect(workStateLabel("creating")).toBe("Preparing guide");
    expect(workStateLabel("generating")).toBe("Generating guide");
    expect(workStateLabel("revising")).toBe("Updating guide");
    expect(workStateLabel("registering")).toBe("Reading source");
    expect(workStateLabel("removing")).toBe("Removing source");
    expect(workStateLabel("opening")).toBe("Opening guide");
    expect(workStateLabel("renaming")).toBe("Saving guide name");
    expect(workStateLabel("retrying")).toBe("Retrying guide");
    expect(workStateLabel("deleting")).toBe("Deleting guide");
    expect(workStateLabel("exporting")).toBe("Saving HTML file");
    expect(isSourceReadError("The stored source could not be read.")).toBe(true);
    expect(isSourceReadError("The model rejected the guide.")).toBe(false);
    expect(sourceRecoveryMessageForGuide(exportGuide({
      source: null,
      error: "The stored source could not be read.",
    }))).toBe("The stored source could not be read.");
    expect(sourceRecoveryMessageForGuide(exportGuide({ error: "The model rejected the guide." }))).toBeNull();
  });

  it("clears stale source recovery messages after a healthy guide response", () => {
    const unavailable = exportGuide({
      source: null,
      source_error: "The stored source could not be read. Choose the source again in the Source section.",
    });
    const healthy = exportGuide({ source: reorderedImageSource });

    expect(sourceErrorForGuideResponse(healthy)).toBeNull();
    expect(sourceRecoveryMessageForGuide({
      ...healthy,
      error: "The stored source could not be read.",
    })).toBeNull();
    expect(sourceErrorForGuideResponse(unavailable)).toBe(unavailable.source_error);
    expect(mergeGuideResponse(unavailable, healthy)).toMatchObject({
      source: reorderedImageSource,
    });
    expect(mergeGuideResponse(unavailable, healthy)?.source_error).toBeUndefined();
  });

  it("keeps the active source warning when another history guide changes", () => {
    const activeWarning = "The current source needs repair.";
    const backgroundHealthy = exportGuide({
      guide_id: "background-guide",
      source_id: "source-2",
      source: reorderedImageSource,
      error: "The stored source could not be read.",
    });
    const backgroundFailed = exportGuide({
      guide_id: "background-guide",
      source_id: "source-2",
      source: null,
      source_error: "The background source could not be read.",
    });
    const activeHealthy = exportGuide({
      source: { ...reorderedImageSource, source_id: "source-1" },
    });
    const activeFailed = exportGuide({
      source: null,
      source_error: "The current source could not be read.",
    });

    expect(sourceErrorAfterGuideAction("guide-export", "source-1", backgroundHealthy, activeWarning)).toBe(
      activeWarning,
    );
    expect(sourceErrorAfterGuideAction("guide-export", "source-1", backgroundFailed, activeWarning)).toBe(
      activeWarning,
    );
    expect(sourceErrorAfterGuideAction("guide-export", "source-1", activeHealthy, activeWarning)).toBeNull();
    expect(sourceErrorAfterGuideAction("guide-export", "source-1", activeFailed, null)).toBe(
      activeFailed.source_error,
    );
  });

  it("derives readable safe names, protects reserved names, and adds custom ranges", () => {
    expect(exportFilename(exportGuide())).toBe("cell-division-pages-2-3.html");
    expect(exportFilename(exportGuide({ name: "../ Unsafe / Guide" }))).toBe(
      "unsafe-guide-pages-2-3.html",
    );
    expect(exportFilename(exportGuide({ name: "CON", selection: { mode: "all" } }))).toBe(
      "study-con.html",
    );
    expect(exportFilename(exportGuide({ name: "COM2", selection: { mode: "all" } }))).toBe(
      "study-com2.html",
    );
    expect(exportFilename(exportGuide({ name: "LPT9", selection: { mode: "all" } }))).toBe(
      "study-lpt9.html",
    );
    expect(exportFilename(exportGuide({ name: "   ", selection: { mode: "all" } }))).toBe(
      "study-guide.html",
    );
  });

  it("disables export without a saved artifact or while another request is busy", () => {
    const guideWithoutArtifact = exportGuide({ artifact_url: null });

    expect(canExportGuide(null, null)).toBe(false);
    expect(canExportGuide(guideWithoutArtifact, null)).toBe(false);
    expect(canExportGuide(exportGuide({ status: "needs-attention" }), null)).toBe(false);
    expect(canExportGuide(exportGuide({ status: "failed" }), null)).toBe(false);
    expect(canExportGuide(exportGuide(), "revising")).toBe(false);
    expect(canExportGuide(exportGuide(), null)).toBe(true);

    const markup = renderToStaticMarkup(
      <ExportAction
        disabled={!canExportGuide(guideWithoutArtifact, null)}
        onClick={() => undefined}
      />,
    );

    expect(markup).toContain('class="secondary-button export-button"');
    expect(markup).toContain('disabled=""');
    expect(markup).toContain("Export HTML");
  });

  it("blocks Forge while the displayed source needs recovery", () => {
    const recoveryError = "The stored source could not be read. Choose the source again in Source.";

    expect(canForgeStudyGuide(true, true, currentImageSource, true, false, recoveryError)).toBe(false);
    expect(canForgeStudyGuide(true, true, currentImageSource, true, false, null)).toBe(true);
  });

  it("fetches the current inline artifact bytes without cache and sends them to the save bridge", async () => {
    const revisedBytes = new Uint8Array([0, 255, 10, 13, 128]).buffer;
    const requests: Array<{ url: string; init: RequestInit | undefined }> = [];
    const fetchArtifact = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      requests.push({ url: String(input), init });
      return {
        ok: true,
        arrayBuffer: async () => revisedBytes,
      } as Response;
    };
    const saveArtifact = vi.fn(async (name: string, bytes: ArrayBuffer) => {
      expect(name).toBe("cell-division-pages-2-3.html");
      expect(bytes).toBe(revisedBytes);
      return { canceled: false, path: "C:/saved/cell-division-pages-2-3.html" };
    });

    const result = await exportGuideArtifact(
      exportGuide({ revision_count: 2 }),
      "http://127.0.0.1:53124",
      saveArtifact,
      fetchArtifact,
    );

    expect(requests).toEqual([{
      url: "http://127.0.0.1:53124/api/guides/guide-export/artifact.html",
      init: { cache: "no-store" },
    }]);
    expect(saveArtifact).toHaveBeenCalledTimes(1);
    expect(result).toEqual({
      filename: "cell-division-pages-2-3.html",
      canceled: false,
      path: "C:/saved/cell-division-pages-2-3.html",
    });
  });

  it("keeps cancellation as a non-success result and reports the saved path only on success", () => {
    expect(exportFeedbackMessage(
      { canceled: true, path: null },
      "cell-division-pages-2-3.html",
    )).toBe("Export canceled.");
    expect(exportFeedbackMessage(
      { canceled: false, path: "C:/exports/renamed-by-user.html" },
      "cell-division-pages-2-3.html",
    )).toBe("Saved to C:/exports/renamed-by-user.html.");
    expect(exportFeedbackMessage(
      { canceled: false, path: null },
      "cell-division-pages-2-3.html",
    )).toBe("Saved cell-division-pages-2-3.html.");
  });

  it("clears old success feedback when the guide or source context changes", () => {
    const successFeedback = { kind: "success" as const, message: "Saved to C:/exports/guide.html." };
    const originalContext = exportFeedbackContextKey(exportGuide(), "source-1");

    expect(
      exportFeedbackAfterContextChange(
        successFeedback,
        originalContext,
        exportFeedbackContextKey(exportGuide({ name: "Renamed guide" }), "source-1"),
      ),
    ).toBeNull();
    expect(
      exportFeedbackAfterContextChange(
        successFeedback,
        originalContext,
        exportFeedbackContextKey(exportGuide({ revision_count: 2 }), "source-1"),
      ),
    ).toBeNull();
    expect(
      exportFeedbackAfterContextChange(
        successFeedback,
        originalContext,
        exportFeedbackContextKey(exportGuide(), "source-2"),
      ),
    ).toBeNull();
    expect(
      exportFeedbackAfterContextChange(successFeedback, originalContext, exportFeedbackContextKey(null, null)),
    ).toBeNull();
    expect(exportFeedbackAfterContextChange(successFeedback, originalContext, originalContext)).toBe(
      successFeedback,
    );
  });

  it("does not call the save bridge when the current artifact fetch fails", async () => {
    const saveArtifact = vi.fn();
    const fetchArtifact = async (): Promise<Response> => ({
      ok: false,
      status: 503,
    } as Response);

    await expect(
      exportGuideArtifact(exportGuide(), "http://127.0.0.1:53124", saveArtifact, fetchArtifact),
    ).rejects.toThrow("HTTP 503");
    expect(saveArtifact).not.toHaveBeenCalled();
  });

  it("restores a saved image source from stored names without fake local paths", () => {
    const storedSource: SourceView = {
      source_id: "stored-source",
      kind: "images",
      display_name: "first.png",
      files: ["first.png", "second.png"],
      page_count: null,
      image_count: 2,
      total_bytes: 42,
      created_at: "2026-09-19T00:00:00Z",
    };

    const restored = draftFromStoredSource(storedSource);

    expect(restored.paths).toEqual([]);
    expect(restored.registeredPaths).toEqual([]);
    expect(restored.originalPathsAvailable).toBe(false);
    expect(restored.imageFiles.map((file) => file.name)).toEqual(["first.png", "second.png"]);
    expect(restored.imageFiles.every((file) => file.path === undefined)).toBe(true);
  });

  it("does not re-register a reopened source when forging from its stored copy", () => {
    const storedSource: SourceView = {
      source_id: "stored-source",
      kind: "pdf",
      display_name: "course.pdf",
      files: ["course.pdf"],
      page_count: 8,
      image_count: null,
      total_bytes: 42,
      created_at: "2026-09-19T00:00:00Z",
    };

    expect(sourcePathsForForge(draftFromStoredSource(storedSource))).toEqual([]);
  });

  it("ignores an out-of-order startup history response after a newer refresh or mutation", () => {
    const guard = createHistoryLoadGuard();
    const startupRequest = guard.begin();
    const refreshRequest = guard.begin();

    expect(guard.isCurrent(startupRequest)).toBe(false);
    expect(guard.isCurrent(refreshRequest)).toBe(true);

    const mutationVersion = guard.invalidate();

    expect(guard.isCurrent(refreshRequest)).toBe(false);
    expect(guard.isCurrent(mutationVersion)).toBe(true);
  });

  it("chooses source recovery after deleting the last guide for a displayed source", () => {
    const localGuide: GuideSummary = {
      guide_id: "guide-local",
      source_id: "source-1",
      selection: { mode: "images" },
      name: "Local guide",
      status: "ok",
      created_at: "2026-09-19T00:00:00Z",
      updated_at: "2026-09-19T00:00:00Z",
      error: null,
      findings: [],
      revision_count: 0,
      artifact_url: "/api/guides/guide-local/artifact.html",
      source: { ...reorderedImageSource, source_id: "source-1" },
    };
    const duplicateGuide = { ...localGuide, guide_id: "guide-duplicate" };
    const activeGuide = { ...localGuide, guide_id: "guide-active" };

    expect(
      displayedSourceRecoveryAfterGuideDeletion(currentImageSource, [localGuide], null, "guide-local"),
    ).toBe("reregister");
    expect(
      displayedSourceRecoveryAfterGuideDeletion(
        { ...currentImageSource, originalPathsAvailable: false },
        [localGuide],
        null,
        "guide-local",
      ),
    ).toBe("clear");
    expect(
      displayedSourceRecoveryAfterGuideDeletion(
        currentImageSource,
        [localGuide, duplicateGuide],
        null,
        "guide-local",
      ),
    ).toBe("keep");
    expect(
      displayedSourceRecoveryAfterGuideDeletion(currentImageSource, [localGuide], activeGuide, "guide-local"),
    ).toBe("keep");
  });

  it("updates the active guide and exactly one matching history entry after rename", () => {
    const original: GuideView = {
      guide_id: "guide-1",
      source_id: "source-1",
      selection: { mode: "all" },
      name: "Old name",
      status: "ok",
      created_at: "2026-09-19T00:00:00Z",
      updated_at: "2026-09-19T00:00:00Z",
      error: null,
      findings: [],
      revision_count: 0,
      artifact_url: "/api/guides/guide-1/artifact.html",
    };
    const duplicateSourceGuide = { ...original, guide_id: "guide-2", name: "Other guide" };
    const renamed = { ...original, name: "New name" };

    const history = replaceGuideInHistory([original, duplicateSourceGuide], renamed);

    expect(history.map((item) => [item.guide_id, item.name])).toEqual([
      ["guide-1", "New name"],
      ["guide-2", "Other guide"],
    ]);
  });

  it("clears a stale source recovery message in history after a healthy update", () => {
    const unavailable: GuideSummary = {
      ...exportGuide({ source: null }),
      source_error: "Choose the source again in the Source section.",
    };
    const healthy: GuideSummary = {
      ...exportGuide({ source: reorderedImageSource }),
    };

    const restored = replaceGuideInHistory([unavailable], healthy)[0];
    const stillUnavailable = replaceGuideInHistory([unavailable], unavailable)[0];

    expect(restored.source_error).toBeUndefined();
    expect(stillUnavailable.source_error).toBe(unavailable.source_error);
  });

  it("shows the saved guide name, status, and current artifact preview together", () => {
    const guide: GuideView = {
      guide_id: "guide-1",
      source_id: "source-1",
      selection: { mode: "all" },
      name: "Saved guide",
      status: "ok",
      created_at: "2026-09-19T00:00:00Z",
      updated_at: "2026-09-19T00:00:00Z",
      error: null,
      findings: [],
      revision_count: 0,
      artifact_url: "/api/guides/guide-1/artifact.html",
    };

    const markup = renderToStaticMarkup(
      <GuideCard guide={guide} baseUrl="http://127.0.0.1:53124" />,
    );

    expect(markup).toContain("Saved guide");
    expect(markup).toContain("Guide ready");
    expect(markup).toMatch(/srcdoc=/i);
    expect(markup).not.toContain('src="http://127.0.0.1:53124/api/guides/guide-1/artifact.html"');
    expect(markup).toContain("Loading saved guide preview");
  });

  it("keeps parser diagnostics out of the visible source recovery message", () => {
    const guide = exportGuide({
      source: null,
      status: "failed",
      artifact_url: null,
      error: "The stored source could not be read. Choose the source again in the Source section.",
      findings: ["Failed to open file 'course.pdf' as type pdf."],
    });

    const markup = renderToStaticMarkup(
      <GuideCard guide={guide} baseUrl="http://127.0.0.1:53124" />,
    );

    expect(markup).toContain("Choose the source again in the Source section.");
    expect(markup).not.toContain("Failed to open file");
  });

  it("keeps a guide selection anchor relative to the artifact viewer", () => {
    expect(selectionRectInGuideViewer(
      { left: 200, top: 180, right: 600, bottom: 500 },
      { left: 24, top: 40, right: 176, bottom: 76 },
      { left: 100, top: 120, right: 700, bottom: 620 },
    )).toEqual({ left: 124, top: 100, right: 276, bottom: 136 });
  });

  it("keeps the revision popup inside the generated guide viewer", () => {
    const guide: GuideView = {
      guide_id: "guide-1",
      source_id: "source-1",
      selection: { mode: "all" },
      name: "Saved guide",
      status: "ok",
      created_at: "2026-09-19T00:00:00Z",
      updated_at: "2026-09-19T00:00:00Z",
      error: null,
      findings: [],
      revision_count: 0,
      artifact_url: "/api/guides/guide-1/artifact.html",
    };
    const markup = renderToStaticMarkup(
      <GuideCard
        guide={guide}
        baseUrl="http://127.0.0.1:53124"
        revisionPopup={
          <RevisionPopup
            selectedText="Selected text"
            anchorRect={{ left: 80, top: 100, right: 180, bottom: 124 }}
            viewerBounds={{ left: 0, top: 0, right: 500, bottom: 400 }}
            onClarify={() => undefined}
            onUpdate={() => undefined}
            onClose={() => undefined}
          />
        }
      />,
    );

    expect(markup).toContain('class="artifact-frame"');
    expect(markup).toMatch(/srcdoc=/i);
    expect(markup).not.toContain('src="http://127.0.0.1:53124/api/guides/guide-1/artifact.html"');
    expect(markup).toContain('sandbox="allow-same-origin"');
    expect(markup).not.toContain("allow-scripts");
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("Selected text");
  });

  it("requests the stored artifact without reusing a browser cache entry", () => {
    const controller = new AbortController();

    expect(artifactFetchOptions(controller.signal)).toEqual({
      cache: "no-store",
      signal: controller.signal,
    });
  });

  it("keeps the study guide tab inactive until a job exists", () => {
    const markup = renderToStaticMarkup(<ViewerTabs hasJob={false} />);

    expect(markup).toContain('role="tab"');
    expect(markup).toContain('aria-selected="false">Study guide</button>');
    expect(markup).not.toContain('class="viewer-tab is-active"');
  });

  it("marks the guide tab active after a job exists", () => {
    const markup = renderToStaticMarkup(<ViewerTabs hasJob />);

    expect(markup).toContain('class="viewer-tab is-active"');
    expect(markup).toContain('aria-selected="true">Study guide</button>');
  });

  it("keeps Source usable while the unavailable Study guide tab stays disabled", () => {
    const markup = renderToStaticMarkup(
      <ViewerTabs
        hasJob={false}
        hasSource
        activeTab="source"
        onTabChange={() => undefined}
      />,
    );

    expect(markup).toContain('aria-selected="true">Source</button>');
    expect(markup).toContain('aria-selected="false">Study guide</button>');
    expect(markup.match(/<button[^>]*disabled=""[^>]*>Study guide/g)).toHaveLength(1);
    expect(markup).not.toContain('disabled="">Source</button>');
  });

  it("disables both viewer tabs while a conflicting request is running", () => {
    const markup = renderToStaticMarkup(
      <ViewerTabs
        hasJob
        hasSource
        activeTab="guide"
        disabled
        onTabChange={() => undefined}
      />,
    );

    expect(markup.match(/<button[^>]*disabled=""[^>]*>/g)).toHaveLength(2);
  });

  it("shows Remove as an active source action", () => {
    const markup = renderToStaticMarkup(<SourceRemoveAction onClick={() => undefined} />);

    expect(markup).toContain(">Remove</button>");
    expect(markup).not.toContain('disabled=""');
  });

  it("uses one branded navigation bar without fake window controls", () => {
    const nav = (AppModule as unknown as { StudyForgeNav?: () => ReactElement }).StudyForgeNav;

    expect(nav).toBeDefined();
    if (nav === undefined) return;

    const markup = renderToStaticMarkup(nav());

    expect(markup).toContain('class="app-nav"');
    expect(markup).toContain("Study Forge");
    expect(markup).not.toContain("window-button");
  });

  it("keeps the navigation label focused on Study Forge", () => {
    const nav = (AppModule as unknown as { StudyForgeNav?: () => ReactElement }).StudyForgeNav;

    expect(nav).toBeDefined();
    if (nav === undefined) return;

    const markup = renderToStaticMarkup(nav());

    expect(markup).not.toContain("Lesson workspace");
  });

  it("keeps the PDF selector to one mode and one optional range", () => {
    const markup = renderToStaticMarkup(
      <PdfRangeSelector
        pageCount={20}
        mode="custom"
        start={1}
        end={3}
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain("Entire document");
    expect(markup).toContain("Custom range");
    expect(markup).toContain('name="pdf-start"');
    expect(markup).toContain('name="pdf-end"');
    expect(markup).not.toContain("Source units");
  });

  it("renders source intake as the selected-source control", () => {
    const source: SourceDraft = {
      kind: "pdf",
      sourceId: "source-1",
      displayName: "course.pdf",
      metadata: { totalBytes: 42 },
      imageFiles: [],
      pageCount: 20,
      paths: ["C:/notes/course.pdf"],
    };
    const markup = renderToStaticMarkup(
      <SourceIntake
        source={source}
        onPathsSelected={() => undefined}
        onRemove={() => undefined}
      />,
    );

    expect(markup).toContain("course.pdf");
    expect(markup).toContain(">Remove</button>");
  });

  it("locks source controls for every non-null work state", () => {
    expect(sourceControlsLocked(null)).toBe(false);
    expect(sourceControlsLocked("registering")).toBe(true);
    expect(sourceControlsLocked("removing")).toBe(true);
    expect(sourceControlsLocked("creating")).toBe(true);
    expect(sourceControlsLocked("generating")).toBe(true);
    expect(sourceControlsLocked("revising")).toBe(true);
  });

  it("registers reordered images before committing the new source and cleaning up the old one", async () => {
    const events: string[] = [];
    let committed: SourceDraft | null = currentImageSource;

    await synchronizeImageSource(
      {
        registerSource: async (paths) => {
          events.push(`register:${paths.join("|")}`);
          return reorderedImageSource;
        },
        removeSource: async (sourceId) => {
          events.push(`remove:${sourceId}`);
          return { source_id: sourceId, deleted: true };
        },
      },
      currentImageSource,
      [currentImageFiles[1], currentImageFiles[0]],
      (nextSource) => {
        committed = nextSource;
        events.push(`commit:${nextSource?.sourceId ?? "none"}`);
      },
    );

    expect(events).toEqual([
      "register:C:/notes/second.png|C:/notes/first.png",
      "commit:source-2",
      "remove:source-1",
    ]);
    expect(committed?.sourceId).toBe("source-2");
    expect(committed?.paths).toEqual([
      "C:/notes/second.png",
      "C:/notes/first.png",
    ]);
  });

  it("treats reordered or cleared image groups as a new active source context", () => {
    expect(sourceIdentityChanged(currentImageSource, reorderedImageSource)).toBe(true);
    expect(sourceIdentityChanged(currentImageSource, null)).toBe(true);
    expect(sourceIdentityChanged(currentImageSource, currentImageSource)).toBe(false);
  });

  it("removes the active source before clearing an image group that became empty", async () => {
    const events: string[] = [];
    let committed: SourceDraft | null = currentImageSource;

    await synchronizeImageSource(
      {
        registerSource: async () => {
          throw new Error("an empty image group must not be registered");
        },
        removeSource: async (sourceId) => {
          events.push(`remove:${sourceId}`);
          return { source_id: sourceId, deleted: true };
        },
      },
      currentImageSource,
      [],
      (nextSource) => {
        committed = nextSource;
        events.push(`commit:${nextSource?.sourceId ?? "none"}`);
      },
    );

    expect(events).toEqual(["remove:source-1", "commit:none"]);
    expect(committed).toBeNull();
  });

  it("does not commit or remove the old source when registration fails", async () => {
    const events: string[] = [];

    await expect(
      synchronizeImageSource(
        {
          registerSource: async () => {
            events.push("register");
            throw new Error("source registration failed");
          },
          removeSource: async () => {
            events.push("remove");
            return { source_id: "source-1", deleted: true };
          },
        },
        currentImageSource,
        [currentImageFiles[1]],
        () => events.push("commit"),
      ),
    ).rejects.toThrow("source registration failed");

    expect(events).toEqual(["register"]);
  });

  it("keeps the matching replacement committed when old-source cleanup fails", async () => {
    const events: string[] = [];
    let committed: SourceDraft | null = currentImageSource;

    await expect(
      synchronizeImageSource(
        {
          registerSource: async (paths) => {
            events.push(`register:${paths.join("|")}`);
            return reorderedImageSource;
          },
          removeSource: async (sourceId) => {
            events.push(`remove:${sourceId}`);
            throw new Error("old source cleanup failed");
          },
        },
        currentImageSource,
        [currentImageFiles[1], currentImageFiles[0]],
        (nextSource) => {
          committed = nextSource;
          events.push(`commit:${nextSource?.sourceId ?? "none"}`);
        },
      ),
    ).rejects.toThrow("old source cleanup failed");

    expect(events).toEqual([
      "register:C:/notes/second.png|C:/notes/first.png",
      "commit:source-2",
      "remove:source-1",
    ]);
    expect(committed?.sourceId).toBe("source-2");
    expect(committed?.paths).toEqual([
      "C:/notes/second.png",
      "C:/notes/first.png",
    ]);
  });
});
