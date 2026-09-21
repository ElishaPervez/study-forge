import { memo, useCallback, useEffect, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { createPortal } from "react-dom";

import type { GuideSelection } from "../api";
import { useLeavingValue } from "../useLeavingValue";
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

export const SOURCE_MENU_EXIT_MS = 160;
export const SOURCE_LIGHTBOX_EXIT_MS = 180;

export interface SourceMenuAnchor {
  x: number;
  y: number;
}

export interface SourceMenuViewport {
  width: number;
  height: number;
}

export interface SourceMenuSize {
  width: number;
  height: number;
}

export interface SourceMenuPlacement extends SourceMenuSize {
  left: number;
  top: number;
}

export const SOURCE_MENU_EDGE_PADDING = 8;
const UNMEASURED_SOURCE_MENU_SIZE: SourceMenuSize = { width: 172, height: 72 };

interface ContextMenuState {
  pageNumber: number;
  anchor: SourceMenuAnchor;
  viewport: SourceMenuViewport;
}

/**
 * The page menu is painted in a body-level portal so `.main-column`'s paint
 * containment can neither offset it (it establishes the containing block for
 * fixed descendants) nor clip it. It is anchored to viewport coordinates, so
 * keep it at the pointer and slide it back inside the window near an edge.
 */
export function placeSourceMenu(
  anchor: SourceMenuAnchor,
  viewport: SourceMenuViewport,
  size: SourceMenuSize = UNMEASURED_SOURCE_MENU_SIZE,
): SourceMenuPlacement {
  const width = Math.min(size.width, Math.max(0, viewport.width - SOURCE_MENU_EDGE_PADDING * 2));
  const height = Math.min(size.height, Math.max(0, viewport.height - SOURCE_MENU_EDGE_PADDING * 2));

  const minLeft = SOURCE_MENU_EDGE_PADDING;
  const maxLeft = Math.max(minLeft, viewport.width - width - SOURCE_MENU_EDGE_PADDING);
  const left = Math.min(Math.max(anchor.x, minLeft), maxLeft);

  const minTop = SOURCE_MENU_EDGE_PADDING;
  const maxTop = Math.max(minTop, viewport.height - height - SOURCE_MENU_EDGE_PADDING);
  const below = anchor.y;
  const above = anchor.y - height;
  const opensBelow = below + height <= viewport.height - SOURCE_MENU_EDGE_PADDING;
  const top = opensBelow ? below : Math.min(Math.max(above, minTop), maxTop);

  return { left, top, width, height };
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

/** The one page or image that is currently open in its own enlarged window. */
export interface EnlargedPage {
  ordinal: number;
  alt: string;
  caption: string;
}

export function enlargedPdfPage(pageNumber: number): EnlargedPage {
  return { ordinal: pageNumber, alt: `Page ${pageNumber}`, caption: `Page ${pageNumber}` };
}

export function enlargedImagePage(file: ImageFile, ordinal: number): EnlargedPage {
  return { ordinal, alt: imageAlt(file, ordinal), caption: `${ordinal}. ${file.name}` };
}

/**
 * The enlarged page floats in its own window over a blurred backdrop, so a press
 * that lands on the backdrop itself - and never on that window - retires it.
 */
export function isOutsideEnlargedPage(backdrop: HTMLElement | null, target: Node | null): boolean {
  if (backdrop === null || target === null) return false;
  return target === backdrop;
}

interface PdfPageCardProps {
  source: SourceDraft;
  pageNumber: number;
  inRange: boolean;
  onContextMenu: (event: MouseEvent<HTMLElement>, pageNumber: number) => void;
  onEnlarge: (page: EnlargedPage) => void;
  onSourceError?: (message: string) => void;
}

const PdfPageCard = memo(function PdfPageCard({
  source,
  pageNumber,
  inRange,
  onContextMenu,
  onEnlarge,
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
        <button
          type="button"
          className="source-page-image-button"
          aria-label={`Enlarge page ${pageNumber}`}
          title="Click to enlarge"
          onClick={() => onEnlarge(enlargedPdfPage(pageNumber))}
        >
          <img
            className="source-page-image"
            src={sourcePreviewUrl(source, pageNumber)}
            alt={`Page ${pageNumber}`}
            loading="lazy"
            onError={() => onSourceError?.(SOURCE_READ_FAILURE_MESSAGE)}
          />
        </button>
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
  onEnlarge: (page: EnlargedPage) => void;
  onSourceError?: (message: string) => void;
}

const ImagePageCard = memo(function ImagePageCard({
  source,
  file,
  imageNumber,
  onEnlarge,
  onSourceError,
}: ImagePageCardProps) {
  return (
    <figure className="source-page-card source-image-card">
      <div className="source-page-image-wrap">
        <button
          type="button"
          className="source-page-image-button"
          aria-label={`Enlarge image ${imageNumber}: ${file.name}`}
          title="Click to enlarge"
          onClick={() => onEnlarge(enlargedImagePage(file, imageNumber))}
        >
          <img
            className="source-page-image"
            src={sourcePreviewUrl(source, imageNumber)}
            alt={imageAlt(file, imageNumber)}
            loading="lazy"
            onError={() => onSourceError?.(SOURCE_READ_FAILURE_MESSAGE)}
          />
        </button>
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
  const [measuredMenuSize, setMeasuredMenuSize] = useState<SourceMenuSize | null>(null);
  const [enlargedPage, setEnlargedPage] = useState<EnlargedPage | null>(null);
  const contextMenuSurface = useLeavingValue(contextMenu, SOURCE_MENU_EXIT_MS);
  const enlargedPageSurface = useLeavingValue(enlargedPage, SOURCE_LIGHTBOX_EXIT_MS);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const lightboxRef = useRef<HTMLDivElement | null>(null);
  const lightboxCloseRef = useRef<HTMLButtonElement | null>(null);
  const viewerSelection: GuideSelection = source.kind === "pdf"
    && source.pageCount !== null
    && selection.mode !== "images"
    ? clampViewerSelection(selection, source.pageCount)
    : selection;
  const resetKey = sourceViewerResetKey(source);

  useEffect(() => {
    setContextMenu(null);
  }, [resetKey, disabled]);

  // The enlarged window follows the source, not the work state: swapping the
  // source or its image order retires it, while a queued request leaves the
  // pages readable.
  useEffect(() => {
    setEnlargedPage(null);
  }, [resetKey]);

  const handleEnlarge = useCallback((page: EnlargedPage) => {
    setContextMenu(null);
    setEnlargedPage(page);
  }, []);

  const closeEnlargedPage = useCallback(() => setEnlargedPage(null), []);

  useEffect(() => {
    if (enlargedPage === null) return;
    // Opening hands over the keyboard, so Escape works before anything is clicked.
    lightboxCloseRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setEnlargedPage(null);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [enlargedPage]);

  const handleContextMenu = useCallback((event: MouseEvent<HTMLElement>, pageNumber: number) => {
    event.preventDefault();
    if (disabled) return;
    setContextMenu({
      pageNumber,
      anchor: { x: event.clientX, y: event.clientY },
      viewport: { width: window.innerWidth, height: window.innerHeight },
    });
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

  useEffect(() => {
    if (contextMenu === null) return;

    const menu = menuRef.current;
    if (menu !== null) {
      const rect = menu.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setMeasuredMenuSize({ width: rect.width, height: rect.height });
      }
    }

    const close = () => setContextMenu(null);
    const handlePointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      close();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [contextMenu]);

  const activeMenu = contextMenuSurface.shown;
  const menuPlacement: SourceMenuPlacement | null = activeMenu === null
    ? null
    : placeSourceMenu(
      activeMenu.anchor,
      activeMenu.viewport,
      measuredMenuSize ?? UNMEASURED_SOURCE_MENU_SIZE,
    );
  const menuStyle: CSSProperties | undefined = menuPlacement === null
    ? undefined
    : { left: menuPlacement.left, top: menuPlacement.top };

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
                onEnlarge={handleEnlarge}
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
                onEnlarge={handleEnlarge}
                onSourceError={onSourceError}
              />
            );
          })}
        </div>
      )}

      {contextMenuSurface.shown !== null && source.kind === "pdf"
        ? createPortal(
          <div
            ref={menuRef}
            className={`source-context-menu${contextMenuSurface.leaving ? " is-leaving" : ""}`}
            role={contextMenuSurface.leaving ? undefined : "menu"}
            aria-label={contextMenuSurface.leaving ? undefined : `Page ${contextMenuSurface.shown.pageNumber} actions`}
            aria-hidden={contextMenuSurface.leaving ? true : undefined}
            style={menuStyle}
            onContextMenu={(event) => event.preventDefault()}
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => handleEndpoint("first")}
              tabIndex={contextMenuSurface.leaving ? -1 : undefined}
            >
              Set as first page
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => handleEndpoint("last")}
              tabIndex={contextMenuSurface.leaving ? -1 : undefined}
            >
              Set as last page
            </button>
          </div>,
          document.body,
        )
        : null}

      {enlargedPageSurface.shown !== null
        ? createPortal(
          <div
            ref={lightboxRef}
            className={`source-lightbox${enlargedPageSurface.leaving ? " is-leaving" : ""}`}
            role="presentation"
            aria-hidden={enlargedPageSurface.leaving ? true : undefined}
            onMouseDown={(event) => {
              if (enlargedPageSurface.leaving) return;
              if (isOutsideEnlargedPage(lightboxRef.current, event.target as Node | null)) {
                closeEnlargedPage();
              }
            }}
          >
            <figure
              className="source-lightbox-window"
              role={enlargedPageSurface.leaving ? undefined : "dialog"}
              aria-modal={enlargedPageSurface.leaving ? undefined : "true"}
              aria-label={enlargedPageSurface.leaving ? undefined : `Enlarged ${enlargedPageSurface.shown.caption}`}
            >
              <div className="source-lightbox-bar">
                <p className="source-lightbox-caption">{enlargedPageSurface.shown.caption}</p>
                <button
                  ref={lightboxCloseRef}
                  type="button"
                  className="source-lightbox-close"
                  aria-label="Close enlarged page"
                  title="Close"
                  onClick={closeEnlargedPage}
                >
                  ×
                </button>
              </div>
              <div className="source-lightbox-body">
                <img
                  className="source-lightbox-image"
                  src={sourcePreviewUrl(source, enlargedPageSurface.shown.ordinal)}
                  alt={enlargedPageSurface.shown.alt}
                  decoding="async"
                  onError={() => {
                    closeEnlargedPage();
                    onSourceError?.(SOURCE_READ_FAILURE_MESSAGE);
                  }}
                />
              </div>
            </figure>
          </div>,
          document.body,
        )
        : null}
    </section>
  );
});
