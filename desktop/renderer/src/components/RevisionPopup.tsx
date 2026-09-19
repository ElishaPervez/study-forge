import { useEffect, useRef, useState } from "react";

export interface RevisionRect {
  left: number;
  top: number;
  right?: number;
  bottom?: number;
  width?: number;
  height?: number;
}

export interface RevisionBounds extends RevisionRect {}

export interface RevisionPopupSize {
  width: number;
  height: number;
  gap?: number;
}

export type RevisionPopupPlacement = "above" | "below";

export interface RevisionPopupPosition {
  left: number;
  top: number;
  placement: RevisionPopupPlacement;
}

export interface RevisionPopupProps {
  selectedText: string;
  anchorRect: RevisionRect;
  viewerBounds?: RevisionBounds;
  onClarify: () => void;
  onUpdate: (instruction: string) => void;
  onClose: () => void;
}

const DEFAULT_BOUNDS: RevisionBounds = { left: 0, top: 0, right: 720, bottom: 560 };
const POPUP_EDGE_PADDING = 12;
const UNMEASURED_SIZE: RevisionPopupSize = { width: 320, height: 360, gap: 12 };

export function rectRight(rect: RevisionRect): number {
  return rect.right ?? rect.left + (rect.width ?? 0);
}

export function rectBottom(rect: RevisionRect): number {
  return rect.bottom ?? rect.top + (rect.height ?? 0);
}

export function resizeRevisionBounds(
  viewerBounds: RevisionBounds,
  width: number,
  height: number,
): RevisionBounds {
  return {
    left: viewerBounds.left,
    top: viewerBounds.top,
    right: viewerBounds.left + Math.max(0, width),
    bottom: viewerBounds.top + Math.max(0, height),
  };
}

export function constrainRevisionPopupSize(
  viewerBounds: RevisionBounds,
  size: RevisionPopupSize,
): RevisionPopupSize {
  const availableWidth = Math.max(0, rectRight(viewerBounds) - viewerBounds.left - POPUP_EDGE_PADDING * 2);
  const availableHeight = Math.max(0, rectBottom(viewerBounds) - viewerBounds.top - POPUP_EDGE_PADDING * 2);
  return {
    width: Math.min(size.width, availableWidth),
    height: Math.min(size.height, availableHeight),
    gap: size.gap ?? UNMEASURED_SIZE.gap,
  };
}

export function truncateSelection(selectedText: string, maxLength = 112): string {
  const normalized = selectedText.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return `“${normalized}”`;
  return `“${normalized.slice(0, maxLength).trimEnd()}…”`;
}

export function isRevisionInstructionValid(instruction: string): boolean {
  return instruction.trim().length > 0;
}

export function positionRevisionPopup(
  anchorRect: RevisionRect,
  viewerBounds: RevisionBounds,
  size: RevisionPopupSize = UNMEASURED_SIZE,
): RevisionPopupPosition {
  const gap = size.gap ?? UNMEASURED_SIZE.gap ?? 12;
  const padding = POPUP_EDGE_PADDING;
  const boundsRight = rectRight(viewerBounds);
  const boundsBottom = rectBottom(viewerBounds);
  const anchorBottom = rectBottom(anchorRect);
  const maxLeft = Math.max(viewerBounds.left + padding, boundsRight - size.width - padding);
  const left = Math.min(Math.max(anchorRect.left, viewerBounds.left + padding), maxLeft);
  const belowTop = anchorBottom + gap;
  const aboveTop = anchorRect.top - gap - size.height;

  if (belowTop + size.height <= boundsBottom - padding) {
    return { left, top: belowTop, placement: "below" };
  }
  if (aboveTop >= viewerBounds.top + padding) {
    return { left, top: aboveTop, placement: "above" };
  }

  const maxTop = Math.max(viewerBounds.top + padding, boundsBottom - size.height - padding);
  return {
    left,
    top: Math.min(Math.max(belowTop, viewerBounds.top + padding), maxTop),
    placement: belowTop <= viewerBounds.top + padding ? "below" : "above",
  };
}

export function RevisionPopup({
  selectedText,
  anchorRect,
  viewerBounds = DEFAULT_BOUNDS,
  onClarify,
  onUpdate,
  onClose,
}: RevisionPopupProps) {
  const [instruction, setInstruction] = useState("");
  const popupRef = useRef<HTMLElement>(null);
  const [measuredPopup, setMeasuredPopup] = useState<{
    selection: string;
    size: RevisionPopupSize;
  } | null>(null);
  const [observedViewerBounds, setObservedViewerBounds] = useState<RevisionBounds | null>(null);
  const effectiveViewerBounds = observedViewerBounds ?? viewerBounds;
  const measuredSize = measuredPopup?.selection === selectedText
    ? measuredPopup.size
    : UNMEASURED_SIZE;
  const popupSize = constrainRevisionPopupSize(effectiveViewerBounds, measuredSize);
  const position = positionRevisionPopup(anchorRect, effectiveViewerBounds, popupSize);
  const maxPopupHeight = Math.max(
    0,
    rectBottom(effectiveViewerBounds) - effectiveViewerBounds.top - POPUP_EDGE_PADDING * 2,
  );

  useEffect(() => {
    setInstruction("");
  }, [selectedText]);

  useEffect(() => {
    const popup = popupRef.current;
    if (popup === null) return;

    const measure = () => {
      const rendered = popup.getBoundingClientRect();
      if (rendered.width <= 0 || rendered.height <= 0) return;
      setMeasuredPopup((current) => {
        if (
          current?.selection === selectedText
          && current.size.width === rendered.width
          && current.size.height === rendered.height
        ) {
          return current;
        }
        return {
          selection: selectedText,
          size: {
            width: rendered.width,
            height: rendered.height,
            gap: UNMEASURED_SIZE.gap,
          },
        };
      });
    };

    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(popup);
    return () => observer.disconnect();
  }, [selectedText, viewerBounds]);

  useEffect(() => {
    const popup = popupRef.current;
    const viewer = popup?.parentElement;
    if (viewer === null || viewer === undefined) return;

    const updateViewerBounds = () => {
      const rect = viewer.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const nextBounds = resizeRevisionBounds(viewerBounds, rect.width, rect.height);
      setObservedViewerBounds((current) => {
        if (
          current !== null
          && current.right === nextBounds.right
          && current.bottom === nextBounds.bottom
          && current.left === nextBounds.left
          && current.top === nextBounds.top
        ) {
          return current;
        }
        return nextBounds;
      });
    };

    updateViewerBounds();
    window.addEventListener("resize", updateViewerBounds);
    if (typeof ResizeObserver === "undefined") {
      return () => window.removeEventListener("resize", updateViewerBounds);
    }

    const observer = new ResizeObserver(updateViewerBounds);
    observer.observe(viewer);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", updateViewerBounds);
    };
  }, [viewerBounds]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const update = () => {
    const trimmed = instruction.trim();
    if (trimmed) onUpdate(trimmed);
  };

  return (
    <aside
      ref={popupRef}
      className="revision-popup"
      role="dialog"
      aria-label="Revise selected text"
      data-placement={position.placement}
      style={{
        left: `${position.left}px`,
        top: `${position.top}px`,
        maxHeight: `${maxPopupHeight}px`,
      }}
    >
      <div className="revision-popup-header">
        <span className="revision-popup-kicker">Selected passage</span>
        <button type="button" className="revision-popup-close" aria-label="Close revision popup" onClick={onClose}>
          ×
        </button>
      </div>
      <blockquote className="revision-popup-quote">{truncateSelection(selectedText)}</blockquote>
      <button type="button" className="revision-popup-clarify" onClick={onClarify}>
        Clarify this
      </button>
      <label className="revision-popup-field">
        <span>Or describe a change</span>
        <textarea
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          placeholder="What should change?"
          aria-label="Revision instruction"
          rows={2}
        />
      </label>
      <button
        type="button"
        className="primary-button revision-popup-update"
        onClick={update}
        disabled={!isRevisionInstructionValid(instruction)}
      >
        Update guide
      </button>
    </aside>
  );
}
