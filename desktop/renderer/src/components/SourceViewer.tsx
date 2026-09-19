import { memo, useCallback, useEffect, useState, type CSSProperties, type MouseEvent } from "react";

import type { GuideSelection } from "../api";
import type { PdfSelection } from "./PdfRangeSelector";
import type { ImageFile } from "./ImageGroupEditor";
import type { SourceDraft } from "./SourceIntake";

export interface SourceViewerProps {
  source: SourceDraft;
  selection: GuideSelection;
  onSelectionChange: (selection: GuideSelection) => void;
  disabled?: boolean;
  onSourceError?: (message: string) => void;
}

export const SOURCE_READ_FAILURE_MESSAGE =
  "The stored source could not be read. Choose a different source in Source.";

interface ContextMenuState {
  pageNumber: number;
  left: number;
  top: number;
}

export function sourcePreviewUrl(source: SourceDraft, ordinal: number): string {
  const origin = source.previewBaseUrl?.replace(/\/+$/, "") ?? "";
  const kind = source.kind === "pdf" ? "pages" : "images";
  return `${origin}/api/sources/${encodeURIComponent(source.sourceId)}/${kind}/${ordinal}`;
}

function pageLimit(pageCount: number): number {
  return Number.isSafeInteger(pageCount) && pageCount >= 1 ? pageCount : 1;
}

function clampPage(page: number, pageCount: number): number {
  const limit = pageLimit(pageCount);
  const candidate = Number.isFinite(page) ? Math.trunc(page) : 1;
  return Math.min(limit, Math.max(1, candidate));
}

export function clampViewerSelection(selection: PdfSelection, pageCount: number): PdfSelection {
  if (selection.mode === "all") return { mode: "all" };

  const start = clampPage(selection.start, pageCount);
  const end = clampPage(selection.end, pageCount);
  return {
    mode: "custom",
    start,
    end: Math.max(start, end),
  };
}

export function setFirstPage(
  selection: PdfSelection,
  pageNumber: number,
  pageCount: number,
): PdfSelection {
  const page = clampPage(pageNumber, pageCount);
  if (selection.mode === "all") {
    return { mode: "custom", start: page, end: pageLimit(pageCount) };
  }

  const current = clampViewerSelection(selection, pageCount);
  if (current.mode === "all") return { mode: "custom", start: page, end: pageLimit(pageCount) };
  return {
    mode: "custom",
    start: page,
    end: Math.max(page, current.end),
  };
}

export function setLastPage(
  selection: PdfSelection,
  pageNumber: number,
  pageCount: number,
): PdfSelection {
  const page = clampPage(pageNumber, pageCount);
  if (selection.mode === "all") {
    return { mode: "custom", start: 1, end: page };
  }

  const current = clampViewerSelection(selection, pageCount);
  if (current.mode === "all") return { mode: "custom", start: 1, end: page };
  return {
    mode: "custom",
    start: Math.min(page, current.start),
    end: page,
  };
}

export type PageEndpointAction = "first" | "last";

export function selectionForPageEndpoint(
  selection: GuideSelection,
  endpoint: PageEndpointAction,
  pageNumber: number,
  pageCount: number,
): GuideSelection {
  const current: PdfSelection = selection.mode === "custom" || selection.mode === "all"
    ? selection
    : { mode: "all" };
  return endpoint === "first"
    ? setFirstPage(current, pageNumber, pageCount)
    : setLastPage(current, pageNumber, pageCount);
}

export function sourceViewerResetKey(source: SourceDraft): string {
  return JSON.stringify([source.sourceId, source.kind, source.pageCount, source.paths]);
}

function isInRange(selection: GuideSelection, pageNumber: number): boolean {
  return selection.mode === "custom"
    && pageNumber >= selection.start
    && pageNumber <= selection.end;
}

function imageAlt(file: ImageFile, ordinal: number): string {
  return `Image ${ordinal}: ${file.name}`;
}

interface PdfPageCardProps {
  source: SourceDraft;
  pageNumber: number;
  inRange: boolean;
  onContextMenu: (event: MouseEvent<HTMLElement>, pageNumber: number) => void;
  onSourceError?: (message: string) => void;
}

const PdfPageCard = memo(function PdfPageCard({
  source,
  pageNumber,
  inRange,
  onContextMenu,
  onSourceError,
}: PdfPageCardProps) {
  return (
    <figure
      className="source-page-card"
      data-page-number={pageNumber}
      data-in-range={inRange}
      onContextMenu={(event) => onContextMenu(event, pageNumber)}
    >
      <div className="source-page-image-wrap">
        <img
          className="source-page-image"
          src={sourcePreviewUrl(source, pageNumber)}
          alt={`Page ${pageNumber}`}
          loading="lazy"
          decoding="async"
          onError={() => onSourceError?.(SOURCE_READ_FAILURE_MESSAGE)}
        />
        {inRange ? <span className="source-page-range-marker">In range</span> : null}
      </div>
      <figcaption>Page {pageNumber}</figcaption>
    </figure>
  );
});

interface ImagePageCardProps {
  source: SourceDraft;
  file: ImageFile;
  imageNumber: number;
  onSourceError?: (message: string) => void;
}

const ImagePageCard = memo(function ImagePageCard({
  source,
  file,
  imageNumber,
  onSourceError,
}: ImagePageCardProps) {
  return (
    <figure className="source-page-card source-image-card">
      <div className="source-page-image-wrap">
        <img
          className="source-page-image"
          src={sourcePreviewUrl(source, imageNumber)}
          alt={imageAlt(file, imageNumber)}
          loading="lazy"
          decoding="async"
          onError={() => onSourceError?.(SOURCE_READ_FAILURE_MESSAGE)}
        />
      </div>
      <figcaption>{imageNumber}. {file.name}</figcaption>
    </figure>
  );
});

export const SourceViewer = memo(function SourceViewer({
  source,
  selection,
  onSelectionChange,
  disabled = false,
  onSourceError,
}: SourceViewerProps) {
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const viewerSelection: GuideSelection = source.kind === "pdf"
    && source.pageCount !== null
    && selection.mode !== "images"
    ? clampViewerSelection(selection, source.pageCount)
    : selection;
  const resetKey = sourceViewerResetKey(source);

  useEffect(() => {
    setContextMenu(null);
  }, [resetKey, disabled]);

  const handleContextMenu = useCallback((event: MouseEvent<HTMLElement>, pageNumber: number) => {
    event.preventDefault();
    if (disabled) return;
    setContextMenu({ pageNumber, left: event.clientX, top: event.clientY });
  }, [disabled]);

  const handleEndpoint = useCallback((endpoint: "first" | "last") => {
    if (source.kind !== "pdf" || source.pageCount === null || contextMenu === null) return;
    const next = selectionForPageEndpoint(
      selection,
      endpoint,
      contextMenu.pageNumber,
      source.pageCount,
    );
    onSelectionChange(next);
    setContextMenu(null);
  }, [contextMenu, onSelectionChange, selection, source.kind, source.pageCount]);

  const menuStyle: CSSProperties | undefined = contextMenu === null
    ? undefined
    : { left: contextMenu.left, top: contextMenu.top };

  return (
    <section className="source-viewer" aria-label="Source viewer" onClick={() => setContextMenu(null)}>
      {source.kind === "pdf" ? (
        <div className="source-page-grid">
          {Array.from({ length: source.pageCount ?? 0 }, (_, index) => {
            const pageNumber = index + 1;
            const inRange = isInRange(viewerSelection, pageNumber);
            return (
              <PdfPageCard
                key={pageNumber}
                source={source}
                pageNumber={pageNumber}
                inRange={inRange}
                onContextMenu={handleContextMenu}
                onSourceError={onSourceError}
              />
            );
          })}
        </div>
      ) : (
        <div className="source-page-grid source-image-grid">
          {source.imageFiles.map((file, index) => {
            const imageNumber = index + 1;
            return (
              <ImagePageCard
                key={`${file.path}-${index}`}
                source={source}
                file={file}
                imageNumber={imageNumber}
                onSourceError={onSourceError}
              />
            );
          })}
        </div>
      )}

      {contextMenu !== null && source.kind === "pdf" ? (
        <div
          className="source-context-menu"
          role="menu"
          aria-label={`Page ${contextMenu.pageNumber} actions`}
          style={menuStyle}
          onClick={(event) => event.stopPropagation()}
        >
          <button type="button" role="menuitem" onClick={() => handleEndpoint("first")}>
            Set as first page
          </button>
          <button type="button" role="menuitem" onClick={() => handleEndpoint("last")}>
            Set as last page
          </button>
        </div>
      ) : null}
    </section>
  );
});
