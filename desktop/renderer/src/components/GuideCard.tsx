import { memo, useEffect, useRef, useState, type ReactNode } from "react";

import { artifactUrl, type GuideView } from "../api";
import { rectBottom, rectRight, type RevisionBounds, type RevisionRect } from "./RevisionPopup";

export interface GuideFrameSelection {
  selectedText: string;
  anchorRect: RevisionRect;
  viewerBounds: RevisionBounds;
}

export interface GuideCardProps {
  guide: GuideView;
  baseUrl: string;
  onSelection?: (selection: GuideFrameSelection) => void;
  revisionPopup?: ReactNode;
  revisionBusy?: boolean;
  reselectText?: string | null;
}

export function artifactFetchOptions(signal?: AbortSignal): RequestInit {
  return signal === undefined ? { cache: "no-store" } : { cache: "no-store", signal };
}

export function selectionRectInViewer(
  frameRect: RevisionRect,
  selectionRect: RevisionRect,
  viewerRect: RevisionRect,
): RevisionRect {
  return {
    left: frameRect.left + selectionRect.left - viewerRect.left,
    top: frameRect.top + selectionRect.top - viewerRect.top,
    right: frameRect.left + rectRight(selectionRect) - viewerRect.left,
    bottom: frameRect.top + rectBottom(selectionRect) - viewerRect.top,
  };
}

function statusLabel(status: string): string {
  if (status === "ok") return "Guide ready";
  if (status === "failed") return "Generation failed";
  if (status === "running") return "Generating guide";
  if (status === "pending") return "Guide queued";
  if (status === "needs-attention") return "Needs attention";
  return status;
}

function statusTone(status: string): string {
  if (status === "ok") return "success";
  if (status === "failed") return "failure";
  if (status === "needs-attention") return "attention";
  if (status === "pending") return "pending";
  return "working";
}

function isSourceRecoveryMessage(message: string | null | undefined): boolean {
  const normalized = message?.toLocaleLowerCase() ?? "";
  return normalized.includes("stored source") && normalized.includes("choose");
}

function normalizedText(value: string): string {
  return value.replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function selectNearestText(document: Document, selectedText: string): boolean {
  const query = normalizedText(selectedText);
  if (!query || document.body === null) return false;

  const walker = document.createTreeWalker(document.body, 4);
  const textNodes: Text[] = [];
  let current = walker.nextNode();
  while (current !== null) {
    if (current.nodeType === 3 && current.textContent !== null) {
      textNodes.push(current as Text);
    }
    current = walker.nextNode();
  }

  const directQuery = selectedText.trim().toLocaleLowerCase();
  for (const node of textNodes) {
    const text = normalizedText(node.data);
    const index = node.data.toLocaleLowerCase().indexOf(directQuery);
    if (index === -1) continue;
    const range = document.createRange();
    range.setStart(node, index);
    range.setEnd(node, index + directQuery.length);
    document.getSelection()?.removeAllRanges();
    document.getSelection()?.addRange(range);
    return true;
  }

  for (const node of textNodes) {
    if (!normalizedText(node.data).includes(query)) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    document.getSelection()?.removeAllRanges();
    document.getSelection()?.addRange(range);
    return true;
  }

  const snippet = query.split(" ").slice(0, 8).join(" ");
  if (!snippet) return false;
  for (const block of document.querySelectorAll("h1, h2, h3, h4, p, li, blockquote, section, article")) {
    if (!normalizedText(block.textContent ?? "").includes(snippet)) continue;
    const range = document.createRange();
    range.selectNodeContents(block);
    document.getSelection()?.removeAllRanges();
    document.getSelection()?.addRange(range);
    return true;
  }
  return false;
}

type ArtifactLoadState = "idle" | "loading" | "ready" | "error";

export const GuideCard = memo(function GuideCard({
  guide,
  baseUrl,
  onSelection,
  revisionPopup,
  revisionBusy = false,
  reselectText = null,
}: GuideCardProps) {
  const hasArtifact = guide.artifact_url !== null;
  const previewUrl = hasArtifact ? artifactUrl(baseUrl, guide.guide_id, false) : null;
  const tone = statusTone(guide.status);
  const displayedError = guide.source !== undefined
    && guide.source !== null
    && isSourceRecoveryMessage(guide.error)
    ? null
    : guide.error;
  const displayedFindings = guide.source_error !== undefined || isSourceRecoveryMessage(guide.error)
    ? []
    : guide.findings;
  const frameRef = useRef<HTMLIFrameElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const [artifactHtml, setArtifactHtml] = useState<string | null>(null);
  const [artifactLoadState, setArtifactLoadState] = useState<ArtifactLoadState>(
    hasArtifact ? "loading" : "idle",
  );
  const [artifactLoadError, setArtifactLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (previewUrl === null) {
      setArtifactHtml(null);
      setArtifactLoadState("idle");
      setArtifactLoadError(null);
      return;
    }

    const controller = new AbortController();
    let active = true;
    setArtifactHtml(null);
    setArtifactLoadState("loading");
    setArtifactLoadError(null);

    void fetch(previewUrl, artifactFetchOptions(controller.signal))
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`The saved guide preview could not be loaded (HTTP ${response.status}).`);
        }
        const html = await response.text();
        if (html.trim().length === 0) {
          throw new Error("The saved guide preview is empty.");
        }
        return html;
      })
      .then((html) => {
        if (!active) return;
        setArtifactHtml(html);
        setArtifactLoadState("ready");
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return;
        setArtifactHtml(null);
        setArtifactLoadState("error");
        setArtifactLoadError(
          error instanceof Error ? error.message : "The saved guide preview could not be loaded.",
        );
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [guide.guide_id, guide.revision_count, previewUrl]);

  useEffect(() => {
    const frame = frameRef.current;
    const preview = previewRef.current;
    if (
      frame === null
      || preview === null
      || onSelection === undefined
      || previewUrl === null
      || artifactHtml === null
    ) return;

    let detachDocument: (() => void) | null = null;

    const attachDocument = () => {
      detachDocument?.();
      detachDocument = null;
      let frameDocument: Document | null = null;
      try {
        frameDocument = frame.contentDocument;
      } catch {
        return;
      }
      if (frameDocument === null) return;

      if (reselectText !== null) selectNearestText(frameDocument, reselectText);

      const handleSelection = () => {
        if (revisionBusy) return;
        const selection = frameDocument?.getSelection();
        if (selection === null || selection === undefined || selection.rangeCount === 0 || selection.isCollapsed) {
          return;
        }
        const selectedText = selection.toString().replace(/\s+/g, " ").trim();
        if (!selectedText) return;

        const rangeRect = selection.getRangeAt(0).getBoundingClientRect();
        const frameRect = frame.getBoundingClientRect();
        const previewRect = preview.getBoundingClientRect();
        if (rangeRect.width === 0 && rangeRect.height === 0) return;

        onSelection({
          selectedText,
          anchorRect: selectionRectInViewer(frameRect, rangeRect, previewRect),
          viewerBounds: {
            left: 0,
            top: 0,
            right: previewRect.width,
            bottom: previewRect.height,
          },
        });
      };

      frameDocument.addEventListener("mouseup", handleSelection);
      frameDocument.addEventListener("keyup", handleSelection);
      frameDocument.addEventListener("selectionchange", handleSelection);
      detachDocument = () => {
        frameDocument?.removeEventListener("mouseup", handleSelection);
        frameDocument?.removeEventListener("keyup", handleSelection);
        frameDocument?.removeEventListener("selectionchange", handleSelection);
      };
    };

    frame.addEventListener("load", attachDocument);
    if (frame.contentDocument?.readyState === "complete") attachDocument();
    return () => {
      frame.removeEventListener("load", attachDocument);
      detachDocument?.();
    };
  }, [artifactHtml, onSelection, previewUrl, reselectText, revisionBusy]);

  return (
    <section className={`guide-card tone-${tone}`} aria-label={`Study guide: ${guide.name}`}>
      <header className="guide-card-header">
        <div className="guide-card-heading">
          <p className="section-label">Study guide</p>
          <h3>{guide.name}</h3>
        </div>
        <div className={`status-pill status-${tone}`} aria-label={`Status: ${statusLabel(guide.status)}`}>
          <span className="status-dot" aria-hidden="true" />
          {statusLabel(guide.status)}
        </div>
      </header>

      <div className="guide-card-body">
        <p className="guide-card-message">
          {guide.status === "ok"
            ? "The guide was generated and saved locally."
            : displayedError ?? "The guide is still being prepared."}
        </p>

        {displayedFindings.length > 0 ? (
          <div className="finding-block">
            <h4>What needs a look</h4>
            <ul>
              {displayedFindings.map((finding, index) => <li key={`${finding}-${index}`}>{finding}</li>)}
            </ul>
          </div>
        ) : null}

        {previewUrl !== null ? (
          <div
            className={`guide-card-preview${revisionBusy ? " is-revision-busy" : ""}`}
            ref={previewRef}
            aria-busy={revisionBusy}
          >
            <div className="preview-heading">
              <span>Preview</span>
              <span className="preview-note">Saved artifact</span>
            </div>
            <div className="artifact-frame-shell">
              <iframe
                key={`${guide.guide_id}-${guide.revision_count}`}
                ref={frameRef}
                className="artifact-frame"
                srcDoc={artifactHtml ?? ""}
                title={`Preview of ${guide.name}`}
                loading="lazy"
                referrerPolicy="no-referrer"
                sandbox="allow-same-origin"
              />
              {artifactLoadState === "loading" ? (
                <div className="artifact-frame-state" role="status" aria-live="polite">
                  <strong>Loading saved guide preview</strong>
                  <span>Retrieving the latest saved artifact.</span>
                </div>
              ) : null}
              {artifactLoadState === "error" ? (
                <div className="artifact-frame-state artifact-frame-state-error" role="alert">
                  <strong>Guide preview unavailable</strong>
                  <span>{artifactLoadError ?? "The saved artifact could not be loaded."}</span>
                </div>
              ) : null}
            </div>
            {revisionBusy ? (
              <div className="revision-busy-overlay" role="status" aria-live="polite">
                <span className="revision-busy-kicker">Study guide</span>
                <strong>Updating guide</strong>
                <p>The selected passage is being revised. Your saved guide is locked until it returns.</p>
              </div>
            ) : null}
            {revisionPopup}
          </div>
        ) : null}
      </div>
    </section>
  );
});
