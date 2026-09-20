import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  artifactUrl,
  createApi,
  type GuideSelection,
  type HistoryEntry,
  type GuideSummary,
  type GuideView,
  type OperationSummary,
  type QueueRow,
  type SourceView,
} from "./api";
import { GenerationQueue, progressDetail } from "./components/GenerationQueue";
import { useGenerationQueue } from "./useGenerationQueue";
import {
  artifactFetchOptions,
  GuideCard,
  type GuideFrameSelection,
} from "./components/GuideCard";
import { ForgingScreen } from "./components/ForgingScreen";
import { HistoryList } from "./components/HistoryList";
import { PdfRangeSelector, type PdfSelection } from "./components/PdfRangeSelector";
import { RevisionPopup } from "./components/RevisionPopup";
import { WindowControls } from "./components/WindowControls";
import { SOURCE_READ_FAILURE_MESSAGE, SourceViewer } from "./components/SourceViewer";
import {
  SourceIntake,
  classifySourcePaths,
  draftFromSource,
  draftFromStoredSource,
  type SourceDraft,
} from "./components/SourceIntake";
import type { ImageFile } from "./components/ImageGroupEditor";
import { GlobalDropIndicator, useGlobalFileDrop } from "./useGlobalFileDrop";

type StartupState = "starting" | "ready" | "error";
export type WorkState =
  | "registering"
  | "removing"
  | "creating"
  | "generating"
  | "opening"
  | "renaming"
  | "retrying"
  | "deleting"
  | "revising"
  | "exporting"
  | null;

export function sourceControlsLocked(workState: WorkState): boolean {
  return workState !== null;
}

export function StartupErrorPanel({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <main className="state-screen state-screen-error" role="alert" aria-live="assertive">
      <div className="state-mark" aria-hidden="true">!</div>
      <p className="section-label">Study Forge</p>
      <h1>The local workspace is unavailable.</h1>
      <p>{error}</p>
      <button type="button" className="primary-button state-retry-button" onClick={onRetry}>
        Try again
      </button>
      <p className="state-footnote">The source stays on this computer.</p>
    </main>
  );
}

export function workStateLabel(workState: WorkState): string {
  if (workState === "creating") return "Preparing guide";
  if (workState === "generating") return "Generating guide";
  if (workState === "revising") return "Updating guide";
  if (workState === "registering") return "Reading source";
  if (workState === "removing") return "Removing source";
  if (workState === "opening") return "Opening guide";
  if (workState === "renaming") return "Saving guide name";
  if (workState === "retrying") return "Retrying guide";
  if (workState === "deleting") return "Deleting guide";
  if (workState === "exporting") return "Saving HTML file";
  return "";
}

export function isSourceReadError(message: string): boolean {
  const normalized = message.toLocaleLowerCase();
  return normalized.includes("stored source") || normalized.includes("source could not be read");
}

export function sourceNeedsRecovery(error: string | null): boolean {
  if (error === null) return false;
  const normalized = error.toLocaleLowerCase();
  return isSourceReadError(error)
    || normalized.includes("source unavailable")
    || normalized.includes("choose the source again");
}

export function sourceRecoveryMessageForGuide(guide: GuideView): string | null {
  if (guide.source !== undefined && guide.source !== null) return null;
  return guide.source_error
    ?? (guide.error !== null && isSourceReadError(guide.error) ? guide.error : null);
}

export function sourceErrorForGuideResponse(guide: GuideView): string | null {
  return sourceRecoveryMessageForGuide(guide);
}

export function mergeGuideResponse(
  current: GuideView | null,
  updated: GuideView,
): GuideView | null {
  if (current === null || current.guide_id !== updated.guide_id) return current;
  return {
    ...updated,
    source: updated.source ?? current.source,
  };
}

export function sourceErrorActionTargetsContext(
  activeGuideId: string | null,
  activeSourceId: string | null,
  response: GuideView,
): boolean {
  return activeGuideId === response.guide_id
    && (activeSourceId === null || activeSourceId === response.source_id);
}

export function sourceErrorAfterGuideAction(
  activeGuideId: string | null,
  activeSourceId: string | null,
  response: GuideView,
  currentError: string | null,
): string | null {
  return sourceErrorActionTargetsContext(activeGuideId, activeSourceId, response)
    ? sourceErrorForGuideResponse(response)
    : currentError;
}

export interface ArtifactSaveResult {
  canceled: boolean;
  path: string | null;
}

export interface ExportArtifactResult extends ArtifactSaveResult {
  filename: string;
}

export type ExportFeedback = {
  kind: "success" | "canceled" | "error";
  message: string;
};

type ArtifactSave = (
  defaultName: string,
  bytes: ArrayBuffer,
) => Promise<ArtifactSaveResult>;

const RESERVED_EXPORT_NAMES = new Set([
  "CON",
  "PRN",
  "AUX",
  "NUL",
  "COM1",
  "COM2",
  "COM3",
  "COM4",
  "COM5",
  "COM6",
  "COM7",
  "COM8",
  "COM9",
  "LPT1",
  "LPT2",
  "LPT3",
  "LPT4",
  "LPT5",
  "LPT6",
  "LPT7",
  "LPT8",
  "LPT9",
]);

export function canExportGuide(guide: GuideView | null, workState: WorkState): boolean {
  return guide !== null
    && guide.status === "ok"
    && typeof guide.artifact_url === "string"
    && guide.artifact_url.trim().length > 0
    && workState === null;
}

export function exportFeedbackContextKey(guide: GuideView | null, sourceId: string | null): string {
  return JSON.stringify({
    guideId: guide?.guide_id ?? null,
    guideName: guide?.name ?? null,
    revisionCount: guide?.revision_count ?? null,
    artifactUrl: guide?.artifact_url ?? null,
    sourceId,
  });
}

export function exportFeedbackAfterContextChange(
  feedback: ExportFeedback | null,
  previousContextKey: string,
  nextContextKey: string,
): ExportFeedback | null {
  return previousContextKey === nextContextKey ? feedback : null;
}

export function exportFilename(guide: GuideView): string {
  const asciiName = guide.name.replace(/[^\x00-\x7F]/g, "");
  let base = asciiName.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "study-guide";
  if (RESERVED_EXPORT_NAMES.has(base.toUpperCase())) base = `study-${base}`;
  if (
    guide.selection.mode === "custom"
    && Number.isInteger(guide.selection.start)
    && Number.isInteger(guide.selection.end)
  ) {
    base = `${base}-pages-${guide.selection.start}-${guide.selection.end}`;
  }
  return `${base.toLowerCase()}.html`;
}

export function exportFeedbackMessage(result: ArtifactSaveResult, filename: string): string {
  if (result.canceled) return "Export canceled.";
  return result.path === null
    ? `Saved ${filename}.`
    : `Saved to ${result.path}.`;
}

export async function exportGuideArtifact(
  guide: GuideView,
  baseUrl: string,
  saveArtifact: ArtifactSave,
  fetchArtifact: typeof fetch = fetch,
): Promise<ExportArtifactResult> {
  const filename = exportFilename(guide);
  const response = await fetchArtifact(
    artifactUrl(baseUrl, guide.guide_id, false),
    artifactFetchOptions(),
  );
  if (!response.ok) {
    throw new Error(`The saved guide could not be exported (HTTP ${response.status}).`);
  }
  const bytes = await response.arrayBuffer();
  const result = await saveArtifact(filename, bytes);
  return { filename, ...result };
}

export function ExportAction({
  disabled,
  busy = false,
  feedback,
  onClick,
}: {
  disabled: boolean;
  busy?: boolean;
  feedback?: ExportFeedback | null;
  onClick: () => void;
}) {
  return (
    <>
      <button
        type="button"
        className="secondary-button export-button"
        disabled={disabled}
        aria-busy={busy}
        onClick={onClick}
      >
        {busy ? "Saving..." : "Export HTML file"}
      </button>
      {feedback ? (
        <p
          className={`export-feedback export-feedback-${feedback.kind}`}
          role={feedback.kind === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          {feedback.message}
        </p>
      ) : null}
    </>
  );
}

interface PdfDraftSelection {
  mode: "all" | "custom";
  start: number;
  end: number;
}

export function StudyForgeNav() {
  return (
    <header className="app-nav" aria-label="Study Forge navigation">
      <div className="brand app-nav-brand">
        <div className="brand-mark" aria-hidden="true">S</div>
        <span className="brand-name">Study Forge</span>
      </div>
      <WindowControls />
    </header>
  );
}

export function SidebarToggleIcon({ isCompact }: { isCompact: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <rect x="1.5" y="1.5" width="11" height="11" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
      <line x1="5.2" y1="1.5" x2="5.2" y2="12.5" stroke="currentColor" strokeWidth="1.2" />
      {isCompact ? (
        <path d="M7.8 5.2L9.6 7L7.8 8.8" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" />
      ) : (
        <path d="M9.6 5.2L7.8 7L9.6 8.8" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  );
}

export const SidebarToggle = memo(function SidebarToggle({
  isCompact,
  onToggle,
}: {
  isCompact: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="rail-toggle"
      aria-label={isCompact ? "Expand sidebar" : "Collapse sidebar"}
      aria-expanded={!isCompact}
      title={isCompact ? "Expand sidebar" : "Collapse sidebar"}
      onClick={onToggle}
    >
      <SidebarToggleIcon isCompact={isCompact} />
    </button>
  );
});

export type ViewerTab = "source" | "guide";

export function ViewerTabs({
  hasJob,
  hasSource = hasJob,
  activeTab = hasJob ? "guide" : "source",
  disabled = false,
  onTabChange,
}: {
  hasJob: boolean;
  hasSource?: boolean;
  activeTab?: ViewerTab;
  disabled?: boolean;
  onTabChange?: (tab: ViewerTab) => void;
}) {
  const canChangeTabs = onTabChange !== undefined;
  const sourceIsActive = activeTab === "source" && hasSource;
  const guideIsActive = activeTab === "guide" && hasJob;
  return (
    <div className="viewer-tabs" role="tablist" aria-label="Viewer tabs">
      <button
        type="button"
        className={`viewer-tab${sourceIsActive ? " is-active" : ""}`}
        role="tab"
        disabled={!hasSource || !canChangeTabs || disabled}
        aria-selected={sourceIsActive}
        onClick={() => onTabChange?.("source")}
      >
        Source
      </button>
      <button
        type="button"
        className={`viewer-tab${guideIsActive ? " is-active" : ""}`}
        role="tab"
        disabled={!hasJob || !canChangeTabs || disabled}
        aria-selected={guideIsActive}
        onClick={() => onTabChange?.("guide")}
      >
        Study guide
      </button>
    </div>
  );
}

export function GuideToolbarHeading({ name }: { name: string }) {
  return (
    <div className="viewer-toolbar-guide">
      <p className="section-label">Study guide</p>
      <h2 id="viewer-heading" className="viewer-toolbar-title" title={name}>
        {name}
      </h2>
    </div>
  );
}

export function SourceRemoveAction({
  onClick = () => undefined,
  disabled = false,
}: {
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button type="button" className="source-remove-action" onClick={onClick} disabled={disabled}>
      Remove
    </button>
  );
}

export function sourcePathsForForge(source: SourceDraft): string[] {
  if (source.originalPathsAvailable === false) return [];
  if (source.kind === "pdf") return source.paths.slice(0, 1);
  return source.imageFiles.flatMap((file) => file.path ? [file.path] : []);
}

export function canForgeStudyGuide(
  startupReady: boolean,
  apiAvailable: boolean,
  source: SourceDraft | null,
  pdfSelectionIsValid: boolean,
  busy: boolean,
  sourceError: string | null,
): boolean {
  return startupReady
    && apiAvailable
    && source !== null
    && (source.kind === "pdf" || source.imageFiles.length > 0)
    && pdfSelectionIsValid
    && !busy
    && !sourceNeedsRecovery(sourceError);
}

export function sourceIdentityChanged(
  previousSource: SourceDraft,
  nextSource: SourceDraft | null,
): boolean {
  return nextSource === null || nextSource.sourceId !== previousSource.sourceId;
}

function samePaths(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((path, index) => path === right[index]);
}

interface ImageSourceSyncApi {
  registerSource: (paths: string[]) => Promise<SourceView>;
  removeSource: (sourceId: string) => Promise<unknown>;
}

export async function synchronizeImageSource(
  api: ImageSourceSyncApi,
  currentSource: SourceDraft,
  files: ImageFile[],
  onSourceCommitted: (source: SourceDraft | null) => void,
): Promise<SourceDraft | null> {
  if (currentSource.originalPathsAvailable === false || files.some((file) => !file.path)) {
    throw new Error("Choose the original image files to change their order.");
  }
  const paths = files.map((file) => file.path ?? "");

  if (paths.length === 0) {
    await api.removeSource(currentSource.sourceId);
    onSourceCommitted(null);
    return null;
  }

  const registered = await api.registerSource(paths);
  const nextSource = draftFromSource(registered, paths);
  onSourceCommitted(nextSource);

  if (nextSource.sourceId !== currentSource.sourceId) {
    await api.removeSource(currentSource.sourceId);
  }

  return nextSource;
}

function guideSelection(source: SourceDraft, pdfSelection: PdfDraftSelection): GuideSelection {
  if (source.kind === "images") return { mode: "images" };
  if (pdfSelection.mode === "all") return { mode: "all" };
  return {
    mode: "custom",
    start: pdfSelection.start,
    end: pdfSelection.end,
  };
}

function guideStatusLabel(status: string): string {
  if (status === "ok") return "Guide ready";
  if (status === "failed") return "Generation failed";
  if (status === "running") return "Generating guide";
  if (status === "pending") return "Guide queued";
  if (status === "needs-attention") return "Needs attention";
  return status;
}

/** A guide with a request waiting or running cannot be changed until it settles. */
export function guideIsReadOnly(
  busyGuideIds: ReadonlySet<string>,
  guideId: string | null,
): boolean {
  return guideId !== null && busyGuideIds.has(guideId);
}

/** The newest failed or interrupted request that can still be repeated for a guide. */
export function retryableRequestForGuide(
  history: HistoryEntry[],
  activeGuide: GuideView | null,
  guideId: string,
): OperationSummary | null {
  if (activeGuide !== null && activeGuide.guide_id === guideId) {
    return activeGuide.retryable_request ?? null;
  }
  for (const entry of history) {
    if (entry.kind === "legacy" || entry.guide_id !== guideId) continue;
    return entry.retryable_request ?? null;
  }
  return null;
}

export function queueRowForGuide(rows: QueueRow[], guideId: string | null): QueueRow | null {
  if (guideId === null) return null;
  const own = rows.filter((row) => row.guide_id === guideId);
  if (own.length === 0) return null;
  const active = own.filter((row) => row.state === "running" || row.state === "waiting");
  return (active.length > 0 ? active : [...own].sort((a, b) => b.order - a.order))[0];
}

/**
 * The forging loop stands in for a study guide that has nothing to show yet.
 * It runs only while the request is actually being written: queued work keeps
 * the plain waiting notice, and a guide that already has an artifact keeps that
 * artifact readable while it is updated.
 */
export function guideIsForging(row: QueueRow | null, guide: GuideView | null): boolean {
  return row !== null
    && row.state === "running"
    && guide !== null
    && guide.artifact_url === null;
}

/** What the Study guide area says while this guide's request is queued or running. */
export function queueRowNotice(row: QueueRow | null): string | null {
  if (row === null) return null;
  if (row.state === "waiting") {
    return "This request is waiting its turn. You can keep browsing while it waits.";
  }
  if (row.state === "running") {
    return row.kind === "create"
      ? "This guide is being written now. You can keep browsing while it is prepared."
      : "This guide is being updated. The version shown stays readable until the new one is checked.";
  }
  return null;
}

const CLARIFY_REVISION_INSTRUCTION = "Clarify the selected passage in the study guide.";

export function replaceGuideInHistory(
  guides: HistoryEntry[],
  updatedGuide: GuideView,
): HistoryEntry[] {
  return guides.map((guide) => {
    if (guide.kind === "legacy") return guide;
    if (guide.guide_id !== updatedGuide.guide_id) return guide;
    return {
      ...guide,
      ...updatedGuide,
      source: updatedGuide.source ?? guide.source,
      source_error: updatedGuide.source_error,
    };
  });
}

export interface HistoryLoadGuard {
  begin: () => number;
  invalidate: () => number;
  isCurrent: (version: number) => boolean;
}

export function createHistoryLoadGuard(): HistoryLoadGuard {
  let currentVersion = 0;
  const nextVersion = () => {
    currentVersion += 1;
    return currentVersion;
  };

  return {
    begin: nextVersion,
    invalidate: nextVersion,
    isCurrent: (version) => version === currentVersion,
  };
}

export type DisplayedSourceRecovery = "keep" | "reregister" | "clear";

export function displayedSourceRecoveryAfterGuideDeletion(
  displayedSource: SourceDraft | null,
  guides: HistoryEntry[],
  activeGuide: GuideView | null,
  deletedGuideId: string,
): DisplayedSourceRecovery {
  if (displayedSource === null) return "keep";

  const deletedGuide = guides.find(
    (item): item is GuideSummary => item.kind !== "legacy" && item.guide_id === deletedGuideId,
  );
  if (deletedGuide === undefined || deletedGuide.source_id !== displayedSource.sourceId) {
    return "keep";
  }
  if (activeGuide?.guide_id === deletedGuideId) return "keep";

  const sourceStillReferenced = guides.some(
    (item) => item.kind !== "legacy"
      && item.guide_id !== deletedGuideId
      && item.source_id === deletedGuide.source_id,
  ) || (
    activeGuide !== null
    && activeGuide.guide_id !== deletedGuideId
    && activeGuide.source_id === deletedGuide.source_id
  );
  if (sourceStillReferenced) return "keep";

  return displayedSource.originalPathsAvailable === false ? "clear" : "reregister";
}

function pdfDraftSelection(selection: GuideSelection, pageCount: number | null): PdfDraftSelection {
  if (selection.mode === "custom") {
    return {
      mode: "custom",
      start: selection.start,
      end: selection.end,
    };
  }
  return {
    mode: "all",
    start: 1,
    end: Math.max(1, pageCount ?? 1),
  };
}

export function App() {
  const [startupState, setStartupState] = useState<StartupState>("starting");
  const [startupError, setStartupError] = useState<string | null>(null);
  const [startupAttempt, setStartupAttempt] = useState(0);
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [isSidebarCompact, setIsSidebarCompact] = useState(false);
  const railScrollRef = useRef<HTMLDivElement | null>(null);
  const [source, setSource] = useState<SourceDraft | null>(null);
  const [pdfSelection, setPdfSelection] = useState<PdfDraftSelection>({
    mode: "all",
    start: 1,
    end: 1,
  });
  const [guide, setGuide] = useState<GuideView | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ViewerTab>("source");
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [setupErrorTitle, setSetupErrorTitle] = useState("Could not forge the study guide");
  const [workState, setWorkState] = useState<WorkState>(null);
  const [exportFeedback, setExportFeedback] = useState<ExportFeedback | null>(null);
  const [revisionSelection, setRevisionSelection] = useState<GuideFrameSelection | null>(null);
  const [reselectText, setReselectText] = useState<string | null>(null);
  const historyLoadGuardRef = useRef<HistoryLoadGuard | null>(null);
  const exportBusyRef = useRef(false);
  const forgePendingRef = useRef(false);
  const revisionPendingRef = useRef(false);
  const exportContextKey = exportFeedbackContextKey(guide, source?.sourceId ?? null);
  const exportContextKeyRef = useRef(exportContextKey);
  if (historyLoadGuardRef.current === null) {
    historyLoadGuardRef.current = createHistoryLoadGuard();
  }
  const historyLoadGuard = historyLoadGuardRef.current;

  const api = useMemo(() => (baseUrl === null ? null : createApi(baseUrl)), [baseUrl]);
  // Source work (reading, removing, opening, renaming, deleting, exporting) is a
  // short local action; guide work runs in the background queue and never locks
  // the rest of the app.
  const sourceBusy = sourceControlsLocked(workState);
  const hasGuide = guide !== null;
  const pdfSelectionIsValid = source?.kind !== "pdf"
    || (pdfSelection.start >= 1 && pdfSelection.end >= pdfSelection.start);
  const selectedGuideIdRef = useRef<string | null>(null);
  const sourceIdRef = useRef<string | null>(null);
  const readOnlyRef = useRef(false);
  const viewLoadGuardRef = useRef<HistoryLoadGuard | null>(null);
  if (viewLoadGuardRef.current === null) viewLoadGuardRef.current = createHistoryLoadGuard();
  const viewLoadGuard = viewLoadGuardRef.current;

  useEffect(() => {
    setRevisionSelection(null);
    setReselectText(null);
  }, [activeTab, guide?.guide_id, source?.sourceId]);

  useEffect(() => {
    const previousContextKey = exportContextKeyRef.current;
    exportContextKeyRef.current = exportContextKey;
    setExportFeedback((current) => exportFeedbackAfterContextChange(
      current,
      previousContextKey,
      exportContextKey,
    ));
  }, [exportContextKey]);

  const refreshHistory = async (serviceApi = api, isActive: () => boolean = () => true) => {
    if (serviceApi === null) return;
    const requestVersion = historyLoadGuard.begin();
    try {
      const guides = await serviceApi.listGuides();
      if (!isActive() || !historyLoadGuard.isCurrent(requestVersion)) return;
      setHistory(guides);
      setHistoryError(null);
    } catch (error: unknown) {
      if (!isActive() || !historyLoadGuard.isCurrent(requestVersion)) return;
      setHistoryError(error instanceof Error ? error.message : "Saved guides could not be loaded.");
    }
  };

  async function handleGuideSettled(guideId: string) {
    // A finished request changes saved history. The open document is reloaded
    // only when that same guide is still selected and nothing newer replaced it.
    if (api === null) return;
    await refreshHistory(api);
    if (selectedGuideIdRef.current !== guideId) return;
    const requestVersion = viewLoadGuard.begin();
    try {
      const reloaded = await api.getGuide(guideId);
      if (selectedGuideIdRef.current !== guideId) return;
      if (!viewLoadGuard.isCurrent(requestVersion)) return;
      setGuide((current) => mergeGuideResponse(current, reloaded));
      setSourceError((currentError) => sourceErrorAfterGuideAction(
        selectedGuideIdRef.current,
        sourceIdRef.current,
        reloaded,
        currentError,
      ));
    } catch {
      // The queue panel already explains a failed request.
    }
  }

  const queue = useGenerationQueue({
    api,
    onGuideSettled: (guideId) => {
      void handleGuideSettled(guideId);
    },
  });

  const selectedGuideReadOnly = guideIsReadOnly(queue.busyGuides, guide?.guide_id ?? null);
  const sourceControlsDisabled = sourceBusy || selectedGuideReadOnly;
  const selectedQueueRow = queueRowForGuide(queue.rows, guide?.guide_id ?? null);
  const selectedGuideNotice = queueRowNotice(selectedQueueRow);
  const forgingRow = guideIsForging(selectedQueueRow, guide) ? selectedQueueRow : null;
  const canForge = canForgeStudyGuide(
    startupState === "ready",
    api !== null,
    source,
    pdfSelectionIsValid,
    sourceBusy || queue.submitting || selectedGuideReadOnly,
    sourceError,
  );
  const canExport = canExportGuide(guide, workState);
  selectedGuideIdRef.current = guide?.guide_id ?? null;
  sourceIdRef.current = source?.sourceId ?? null;
  readOnlyRef.current = selectedGuideReadOnly;

  const retryStartup = () => {
    setStartupError(null);
    setStartupState("starting");
    setStartupAttempt((attempt) => attempt + 1);
  };

  useEffect(() => {
    let active = true;

    const loadPort = async () => {
      try {
        if (!window.studyForge) {
          throw new Error("The desktop bridge is unavailable.");
        }
        const port = await window.studyForge.startBackend();
        if (active) {
          const nextBaseUrl = `http://127.0.0.1:${port}`;
          const serviceApi = createApi(nextBaseUrl);
          setBaseUrl(nextBaseUrl);
          setStartupState("ready");
          void refreshHistory(serviceApi, () => active);
        }
      } catch (error: unknown) {
        if (active) {
          setStartupError(error instanceof Error ? error.message : "The local service could not start.");
          setStartupState("error");
        }
      }
    };

    void loadPort();
    return () => {
      active = false;
    };
  }, [startupAttempt]);

  const handlePathsSelected = async (paths: string[]) => {
    if (api === null || sourceBusy) return;

    const classification = classifySourcePaths(paths);
    if ("error" in classification) {
      setSourceError(classification.error);
      return;
    }

    setExportFeedback(null);
    setWorkState("registering");
    setSourceError(null);
    setSetupError(null);
    try {
      const registered = await api.registerSource(paths);
      const nextSource = draftFromSource(registered, paths);
      const previousSource = source;
      setSource(nextSource);
      setPdfSelection({
        mode: "all",
        start: 1,
        end: Math.max(1, nextSource.pageCount ?? 1),
      });
      setGuide(null);
      setActiveTab("source");

      if (previousSource !== null && previousSource.sourceId !== nextSource.sourceId) {
        await api.removeSource(previousSource.sourceId);
      }
    } catch (error: unknown) {
      setSetupErrorTitle("Could not register the source");
      setSourceError(error instanceof Error ? error.message : "The local service rejected this source.");
    } finally {
      setWorkState(null);
    }
  };

  // Global window drop: routes through the same validated intake path as the
  // rail drop card (`classifySourcePaths` -> `handlePathsSelected`).
  const handleWindowFilesDropped = useCallback((files: File[]) => {
    if (!window.studyForge) return;
    const paths = files.map((file) => window.studyForge?.pathForFile(file) ?? "")
      .filter((path) => path.length > 0);
    if (paths.length === 0) {
      setSourceError("Choose one PDF or one or more images.");
      return;
    }
    const classification = classifySourcePaths(paths);
    if ("error" in classification) {
      setSourceError(classification.error);
      return;
    }
    void handlePathsSelected(paths);
    // handlePathsSelected is re-created each render; keying on its inputs keeps the
    // drop callback fresh without re-registering the window listeners every render.
  }, [api, sourceBusy, source]);

  const globalDropOverlayVisible = useGlobalFileDrop({
    disabled: startupState !== "ready" || sourceBusy,
    onFilesDropped: handleWindowFilesDropped,
  });

  const handleImagesChange = (files: ImageFile[]) => {
    if (api === null || source === null || source.kind !== "images" || sourceControlsDisabled) {
      return;
    }

    const previousSource = source;
    setExportFeedback(null);
    setWorkState("registering");
    setSourceError(null);
    setSetupError(null);
    void synchronizeImageSource(api, previousSource, files, (nextSource) => {
      if (sourceIdentityChanged(previousSource, nextSource)) {
        setGuide(null);
        setPdfSelection({ mode: "all", start: 1, end: 1 });
        setActiveTab("source");
        setExportFeedback(null);
        setRevisionSelection(null);
        setReselectText(null);
      }
      setSource(nextSource);
    }).catch((error: unknown) => {
      setSetupErrorTitle("Could not update the image group");
      setSourceError(error instanceof Error ? error.message : "The image group could not be updated.");
    }).finally(() => {
      setWorkState(null);
    });
  };

  const handleRemoveSource = async () => {
    if (api === null || source === null || sourceControlsDisabled) return;

    setExportFeedback(null);
    setWorkState("removing");
    setSourceError(null);
    try {
      await api.removeSource(source.sourceId);
      setSource(null);
      setPdfSelection({ mode: "all", start: 1, end: 1 });
      setActiveTab("source");
      setSetupError(null);
      // A referenced guide is deliberately left untouched. The backend retains
      // its source copy when a guide still points at it.
    } catch (error: unknown) {
      setSetupErrorTitle("Could not remove the source");
      setSourceError(error instanceof Error ? error.message : "The source could not be removed.");
    } finally {
      setWorkState(null);
    }
  };

  const handlePdfSelectionChange = useCallback((selection: PdfSelection) => {
    if (selection.mode === "all") {
      setPdfSelection((current) => ({ ...current, mode: "all" }));
      return;
    }
    setPdfSelection({
      mode: "custom",
      start: selection.start ?? 1,
      end: selection.end ?? selection.start ?? 1,
    });
    setSetupError(null);
  }, []);

  const handleViewerSelectionChange = useCallback((selection: GuideSelection) => {
    if (selection.mode === "images") return;
    handlePdfSelectionChange(selection);
  }, [handlePdfSelectionChange]);

  const handleSourceError = useCallback((message: string) => {
    setSourceError(message);
    setSetupError(null);
  }, []);

  const handleGuideSelection = useCallback((selection: GuideFrameSelection | null) => {
    if (selection === null) {
      setRevisionSelection(null);
      return;
    }
    if (sourceBusy || readOnlyRef.current) return;
    setSetupError(null);
    setRevisionSelection(selection);
  }, [sourceBusy]);

  const handleSidebarToggle = useCallback(() => {
    setIsSidebarCompact((current) => !current);
  }, []);

  const viewerSource = useMemo(() => {
    return source ? { ...source, previewBaseUrl: baseUrl ?? undefined } : null;
  }, [source, baseUrl]);

  const currentViewerSelection = useMemo(() => {
    return source ? guideSelection(source, pdfSelection) : { mode: "all" as const };
  }, [source, pdfSelection]);

  const handleTabChange = (tab: ViewerTab) => {
    if (tab === "source" && source === null) return;
    if (tab === "guide" && guide === null) return;
    setRevisionSelection(null);
    setActiveTab(tab);
  };

  const handleForge = async () => {
    // One Forge action creates one guide and queues writing it. The screen does
    // not wait for the guide: it follows the queue instead.
    if (!canForge || api === null || source === null || forgePendingRef.current) return;
    forgePendingRef.current = true;

    setExportFeedback(null);
    setRevisionSelection(null);
    setReselectText(null);
    setSetupError(null);
    let currentSource = source;
    let createAttempted = false;
    try {
      const paths = sourcePathsForForge(currentSource);
      if (!samePaths(paths, currentSource.registeredPaths ?? currentSource.paths)) {
        setWorkState("registering");
        const registered = await api.registerSource(paths);
        const previousSourceId = currentSource.sourceId;
        currentSource = draftFromSource(registered, paths);
        setSource(currentSource);
        if (previousSourceId !== currentSource.sourceId) {
          await api.removeSource(previousSourceId);
        }
        setWorkState(null);
      }

      const selection = guideSelection(currentSource, pdfSelection);
      historyLoadGuard.invalidate();
      createAttempted = true;
      const outcome = await queue.submitGuide(currentSource.sourceId, selection);
      const accepted = outcome.guide ?? (await api.getGuide(outcome.guide_id));
      setGuide(accepted);
      setActiveTab("guide");
      const sourceRecovery = sourceErrorForGuideResponse(accepted);
      setSourceError(sourceRecovery);
      if (sourceRecovery !== null) setActiveTab("source");
      await refreshHistory();
    } catch (error: unknown) {
      if (createAttempted) await refreshHistory();
      const message = error instanceof Error ? error.message : "The local service rejected the guide.";
      if (isSourceReadError(message)) {
        setSourceError(message);
        setSetupError(null);
      } else {
        setSetupErrorTitle("Could not forge the study guide");
        setSetupError(message);
      }
    } finally {
      forgePendingRef.current = false;
      setWorkState(null);
    }
  };

  const handleRevision = async (mode: "clarify" | "custom", instruction: string) => {
    if (api === null || guide === null || revisionSelection === null) return;
    if (selectedGuideReadOnly || revisionPendingRef.current) return;
    revisionPendingRef.current = true;

    const activeGuideId = guide.guide_id;
    const activeSourceId = source?.sourceId ?? null;
    const selectedText = revisionSelection.selectedText;
    setExportFeedback(null);
    setReselectText(null);
    setSetupError(null);
    setSetupErrorTitle("Could not update the guide");
    historyLoadGuard.invalidate();

    try {
      const outcome = await queue.revise(guide.guide_id, {
        selected_text: selectedText,
        instruction,
        mode,
      });
      // The update is accepted and queued. The entry closes now that the
      // request is recorded, and this guide stays read-only until it settles.
      const accepted = outcome.guide;
      setRevisionSelection(null);
      setReselectText(selectedText);
      if (accepted !== null) {
        setGuide((current) => mergeGuideResponse(current, accepted));
        setSourceError((currentError) => sourceErrorAfterGuideAction(
          activeGuideId,
          activeSourceId,
          accepted,
          currentError,
        ));
        setHistory((current) => replaceGuideInHistory(current, accepted));
      }
      await refreshHistory();
    } catch (error: unknown) {
      setRevisionSelection(null);
      const message = error instanceof Error ? error.message : "The guide could not be updated.";
      if (isSourceReadError(message)) {
        setSourceError(message);
        setSetupError(null);
      } else {
        setSetupError(message);
      }
    } finally {
      revisionPendingRef.current = false;
    }
  };

  const handleExport = async () => {
    if (!canExport || baseUrl === null || guide === null || exportBusyRef.current) return;
    const saveArtifact = window.studyForge?.saveArtifact;
    if (saveArtifact === undefined) {
      setExportFeedback({ kind: "error", message: "The desktop save bridge is unavailable." });
      return;
    }

    exportBusyRef.current = true;
    setExportFeedback(null);
    setWorkState("exporting");
    try {
      const result = await exportGuideArtifact(guide, baseUrl, saveArtifact);
      setExportFeedback({
        kind: result.canceled ? "canceled" : "success",
        message: exportFeedbackMessage(result, result.filename),
      });
    } catch (error: unknown) {
      setExportFeedback({
        kind: "error",
        message: error instanceof Error ? `Export failed: ${error.message}` : "Export failed.",
      });
    } finally {
      exportBusyRef.current = false;
      setWorkState(null);
    }
  };

  const handleOpenGuide = async (summary: { guide_id: string }) => {
    // A guide with a request waiting or running still opens for viewing.
    if (api === null || sourceBusy) return;

    setExportFeedback(null);
    setRevisionSelection(null);
    setReselectText(null);
    setWorkState("opening");
    setSetupError(null);
    const requestVersion = viewLoadGuard.begin();
    try {
      const openedGuide = await api.getGuide(summary.guide_id);
      if (!viewLoadGuard.isCurrent(requestVersion)) return;
      const sourceRecovery = sourceErrorForGuideResponse(openedGuide);
      if (openedGuide.source === undefined || openedGuide.source === null) {
        setSource(null);
        setGuide(null);
        setActiveTab("source");
        setSourceError(sourceRecovery ?? SOURCE_READ_FAILURE_MESSAGE);
        return;
      }
      const restoredSource = {
        ...draftFromStoredSource(openedGuide.source),
        previewBaseUrl: baseUrl ?? undefined,
      };
      setSource(restoredSource);
      setPdfSelection(pdfDraftSelection(openedGuide.selection, restoredSource.pageCount));
      setGuide(openedGuide);
      setHistory((current) => replaceGuideInHistory(current, openedGuide));
      setSourceError(sourceRecovery);
      setActiveTab("guide");
    } catch (error: unknown) {
      setSetupErrorTitle("Could not open the saved guide");
      setSetupError(error instanceof Error ? error.message : "The saved guide could not be opened.");
    } finally {
      setWorkState(null);
    }
  };

  const handleRenameGuide = async (guideId: string, name: string) => {
    if (api === null || sourceBusy) return;

    const activeGuideId = guide?.guide_id ?? null;
    const activeSourceId = source?.sourceId ?? null;
    setExportFeedback(null);
    setWorkState("renaming");
    setSetupError(null);
    try {
      historyLoadGuard.invalidate();
      const renamedGuide = await api.renameGuide(guideId, name);
      setGuide((current) => mergeGuideResponse(current, renamedGuide));
      setSourceError((currentError) => sourceErrorAfterGuideAction(
        activeGuideId,
        activeSourceId,
        renamedGuide,
        currentError,
      ));
      setHistory((current) => replaceGuideInHistory(current, renamedGuide));
      await refreshHistory();
    } catch (error: unknown) {
      setSetupErrorTitle("Could not rename the guide");
      setSetupError(error instanceof Error ? error.message : "The guide name could not be saved.");
    } finally {
      setWorkState(null);
    }
  };

  const handleRetryGuide = async (guideId: string) => {
    if (api === null || sourceBusy) return;

    const activeGuideId = guide?.guide_id ?? null;
    const activeSourceId = source?.sourceId ?? null;
    setExportFeedback(null);
    setRevisionSelection(null);
    setReselectText(null);
    setSetupError(null);
    try {
      historyLoadGuard.invalidate();
      // A failed or interrupted update is retried by its own saved request, so
      // its edit and the guide version it targets are repeated exactly.
      const failedRequest = retryableRequestForGuide(history, guide, guideId);
      const outcome = failedRequest === null
        ? await queue.retryGuide(guideId)
        : await queue.retryOperation(failedRequest.receipt);
      const retriedGuide = outcome.guide;
      if (retriedGuide !== null) {
        setGuide((current) => mergeGuideResponse(current, retriedGuide));
        setHistory((current) => replaceGuideInHistory(current, retriedGuide));
        const sourceRecovery = sourceErrorForGuideResponse(retriedGuide);
        const updatesVisibleSource = sourceErrorActionTargetsContext(
          activeGuideId,
          activeSourceId,
          retriedGuide,
        );
        setSourceError((currentError) => sourceErrorAfterGuideAction(
          activeGuideId,
          activeSourceId,
          retriedGuide,
          currentError,
        ));
        if (updatesVisibleSource && sourceRecovery !== null) {
          setSetupError(null);
          setActiveTab("source");
        }
      }
      await refreshHistory();
    } catch (error: unknown) {
      setSetupErrorTitle("Could not retry the guide");
      setSetupError(error instanceof Error ? error.message : "The guide could not be retried.");
    }
  };

  const handleRetryRequest = async (request: string) => {
    if (api === null || sourceBusy) return;

    setExportFeedback(null);
    setSetupError(null);
    try {
      const outcome = await queue.retryOperation(request);
      const accepted = outcome.guide;
      if (accepted !== null) setGuide((current) => mergeGuideResponse(current, accepted));
      await refreshHistory();
    } catch (error: unknown) {
      setSetupErrorTitle("Could not retry the request");
      setSetupError(error instanceof Error ? error.message : "The request could not be retried.");
    }
  };

  const handleQueueRetry = (row: QueueRow) => {
    if (row.kind === "update" || row.kind === "clarify") {
      void handleRetryRequest(row.receipt);
      return;
    }
    void handleRetryGuide(row.guide_id);
  };

  const handleDeleteGuide = async (guideId: string) => {
    if (api === null || sourceBusy) return;

    setExportFeedback(null);
    setRevisionSelection(null);
    setReselectText(null);
    const displayedSource = source;
    const sourceRecovery = displayedSourceRecoveryAfterGuideDeletion(
      displayedSource,
      history,
      guide,
      guideId,
    );
    const activeGuideDeleted = guide?.guide_id === guideId;
    let sourceRestoreAttempted = false;
    let deletionSucceeded = false;
    const clearDisplayedSource = () => {
      setSource(null);
      setPdfSelection({ mode: "all", start: 1, end: 1 });
      if (activeTab === "source" && guide !== null) setActiveTab("guide");
    };

    setWorkState("deleting");
    setSetupError(null);
    try {
      historyLoadGuard.invalidate();
      await api.deleteGuide(guideId);
      deletionSucceeded = true;
      setHistory((current) => current.filter(
        (item) => item.kind === "legacy" || item.guide_id !== guideId,
      ));
      if (activeGuideDeleted) {
        setGuide(null);
        setSource(null);
        setPdfSelection({ mode: "all", start: 1, end: 1 });
        setActiveTab("source");
      } else if (sourceRecovery === "clear") {
        clearDisplayedSource();
      } else if (sourceRecovery === "reregister" && displayedSource !== null) {
        const paths = sourcePathsForForge(displayedSource);
        if (paths.length === 0) {
          clearDisplayedSource();
        } else {
          sourceRestoreAttempted = true;
          setSource(null);
          const registered = await api.registerSource(paths);
          setSource({
            ...draftFromSource(registered, paths),
            previewBaseUrl: baseUrl ?? undefined,
          });
          setSourceError(null);
        }
      }
      await refreshHistory();
    } catch (error: unknown) {
      if (deletionSucceeded) await refreshHistory();
      if (sourceRestoreAttempted) {
        clearDisplayedSource();
        setSetupErrorTitle("Could not restore the source");
      } else {
        setSetupErrorTitle("Could not delete the guide");
      }
      setSetupError(error instanceof Error ? error.message : "The guide could not be deleted.");
    } finally {
      setWorkState(null);
    }
  };

  if (startupState === "starting") {
    return (
      <main className="state-screen" aria-live="polite">
        <div className="state-mark" aria-hidden="true">S</div>
        <p className="section-label">Study Forge</p>
        <h1>Starting the local workspace...</h1>
        <p>The desktop service is getting ready. Your source stays on this computer.</p>
      </main>
    );
  }

  if (startupState === "error") {
    return (
      <StartupErrorPanel
        error={startupError ?? "The desktop service could not start."}
        onRetry={retryStartup}
      />
    );
  }

  const viewingGuide = activeTab === "guide" && guide !== null;
  const viewerMessage = guide === null
    ? "Forge one guide to see it here."
    : guide.error ?? "The guide is still being prepared.";
  const viewerStatus = guide === null
    ? "Waiting for a source"
    : selectedQueueRow !== null && (selectedQueueRow.state === "running" || selectedQueueRow.state === "waiting")
      ? selectedQueueRow.state === "waiting" ? "Waiting in the queue" : "Writing the guide"
      : guideStatusLabel(guide.status);

  return (
    <>
    <section className="app-shell" aria-label="Study Forge desktop workspace">
      <StudyForgeNav />
      <div className={`app-body ${isSidebarCompact ? "is-rail-compact" : ""}`}>
        <aside className={`rail ${isSidebarCompact ? "is-compact" : ""}`} aria-label="Study guide setup">
          <div className="rail-inner">
            <div className="rail-header">
              <SidebarToggle
                isCompact={isSidebarCompact}
                onToggle={handleSidebarToggle}
              />
              <p className="rail-context" aria-hidden={isSidebarCompact}>Forge / Current guide</p>
            </div>
            <form className="rail-form" onSubmit={(event) => { event.preventDefault(); void handleForge(); }}>
              <div className="rail-scroll" ref={railScrollRef}>
                <SourceIntake
                  source={source}
                  disabled={startupState !== "ready"}
                  busy={sourceBusy}
                  readOnly={selectedGuideReadOnly}
                  error={sourceError}
                  onPathsSelected={handlePathsSelected}
                  onRemove={handleRemoveSource}
                  onImagesChange={handleImagesChange}
                />

                {source?.kind === "pdf" ? (
                  <PdfRangeSelector
                    pageCount={source.pageCount}
                    mode={pdfSelection.mode}
                    start={pdfSelection.start}
                    end={pdfSelection.end}
                    disabled={sourceControlsDisabled}
                    onChange={handlePdfSelectionChange}
                  />
                ) : null}

                {setupError ? (
                  <div className="setup-error" role="alert">
                    <strong>{setupErrorTitle}</strong>
                    <p>{setupError}</p>
                  </div>
                ) : null}

                <HistoryList
                  guides={history}
                  activeGuideId={guide?.guide_id}
                  disabled={sourceBusy}
                  busyGuideIds={queue.busyGuides}
                  scrollContainerRef={railScrollRef}
                  onOpen={handleOpenGuide}
                  onRename={handleRenameGuide}
                  onDelete={handleDeleteGuide}
                  onRetry={handleRetryGuide}
                />
                {historyError ? <p className="history-error" role="alert">{historyError}</p> : null}
              </div>

              <div className="action-stack">
                {workState !== null ? (
                  <p className="work-progress" role="status" aria-live="polite">
                    {workStateLabel(workState)}
                  </p>
                ) : null}
                <button
                  type="submit"
                  className="primary-button generate-button"
                  disabled={!canForge}
                  aria-busy={queue.submitting}
                >
                  {workState === "registering"
                    ? "Preparing source..."
                    : queue.submitting
                      ? "Sending request..."
                      : "Forge study guide"}
                </button>
                <ExportAction
                  disabled={!canExport}
                  busy={workState === "exporting"}
                  feedback={exportFeedback}
                  onClick={() => void handleExport()}
                />
              </div>
            </form>
          </div>
        </aside>

        <main className="main-column" aria-label={activeTab === "source" ? "Source viewer" : "Study guide viewer"}>
          <section className="viewer-shell" aria-label="Source and guide viewer">
            <header className="viewer-toolbar">
              <ViewerTabs
                hasJob={hasGuide}
                hasSource={source !== null}
                activeTab={activeTab}
                onTabChange={handleTabChange}
              />
              {viewingGuide ? <GuideToolbarHeading name={guide.name} /> : null}
              <div className="viewer-toolbar-tools">
                <div className="viewer-status" aria-live="polite">
                  <span className="viewer-status-dot" aria-hidden="true" />
                  <span>{workState !== null ? workStateLabel(workState) : viewerStatus}</span>
                </div>
              </div>
            </header>

            <div className={`viewer-scroll${viewingGuide ? " is-guide-view" : ""}`}>
              {activeTab === "guide" && !viewingGuide ? (
                <div className="viewer-heading-row">
                  <div>
                    <h2 id="viewer-heading">Study guide</h2>
                    <p className="viewer-summary" aria-live="polite">{viewerMessage}</p>
                  </div>
                </div>
              ) : null}

              {activeTab === "source" && viewerSource !== null ? (
                <SourceViewer
                  source={viewerSource}
                  selection={currentViewerSelection}
                  disabled={sourceControlsDisabled}
                  onSelectionChange={handleViewerSelectionChange}
                  onSourceError={handleSourceError}
                />
              ) : activeTab === "guide" && guide !== null && forgingRow !== null ? (
                <ForgingScreen detail={progressDetail(forgingRow)} />
              ) : activeTab === "guide" && guide !== null ? (
                <>
                {selectedGuideNotice !== null ? (
                  <p className="viewer-work-notice" role="status" aria-live="polite">
                    {selectedGuideNotice}
                  </p>
                ) : null}
                <GuideCard
                  key={`${guide.guide_id}-${guide.revision_count}`}
                  guide={guide}
                  baseUrl={baseUrl ?? ""}
                  onSelection={handleGuideSelection}
                  revisionBusy={selectedGuideReadOnly}
                  reselectText={reselectText}
                  revisionPopup={revisionSelection === null ? undefined : (
                    <RevisionPopup
                      selectedText={revisionSelection.selectedText}
                      anchorRect={revisionSelection.anchorRect}
                      viewerBounds={revisionSelection.viewerBounds}
                      onClarify={() => void handleRevision("clarify", CLARIFY_REVISION_INSTRUCTION)}
                      onUpdate={(instruction) => void handleRevision("custom", instruction)}
                      onClose={() => setRevisionSelection(null)}
                    />
                  )}
                />
                </>
              ) : (
                <section className="viewer-empty" aria-labelledby="empty-viewer-heading">
                  <h3>{activeTab === "source" ? "Choose a source" : "Forge one focused guide"}</h3>
                  <p>
                    {activeTab === "source"
                      ? "Choose one PDF or an ordered image group to preview it here."
                      : "Forge one guide to see its status and saved artifact here."}
                  </p>
                </section>
              )}
            </div>
          </section>
        </main>
      </div>
      {globalDropOverlayVisible ? <GlobalDropIndicator /> : null}
    </section>
    <GenerationQueue
      rows={queue.rows}
      connected={queue.connected}
      error={queue.error}
      onOpenGuide={(guideId) => void handleOpenGuide({ guide_id: guideId })}
      onRetry={handleQueueRetry}
      onDismiss={queue.dismiss}
      onDismissFinished={queue.dismissFinished}
    />
    </>
  );
}
