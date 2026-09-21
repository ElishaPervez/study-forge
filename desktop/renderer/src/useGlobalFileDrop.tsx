import { useEffect, useRef, useState } from "react";

import { inAppDragInProgress, trackInAppDrags } from "./inAppDrag";

export interface UseGlobalFileDropOptions {
  disabled: boolean;
  onFilesDropped: (files: File[]) => void;
}

export interface GlobalDropSession {
  depth: number;
  containsFiles: boolean;
}

export function beginGlobalDropSession(): GlobalDropSession {
  return { depth: 0, containsFiles: false };
}

export function dataTransferHasFiles(dataTransfer: DataTransfer | null): boolean {
  if (dataTransfer === null) return false;
  return Array.from(dataTransfer.types).includes("Files");
}

/**
 * A drag only counts as a source drop when it started outside this window:
 * dragging in-app content (a page render, an uploaded image) reports "Files"
 * too, but it carries no file path, so intake could only reject it.
 */
export function sourceDropHasFiles(dataTransfer: DataTransfer | null, inAppDrag: boolean): boolean {
  return !inAppDrag && dataTransferHasFiles(dataTransfer);
}

export function dropSessionAfterEnter(
  session: GlobalDropSession,
  containsFiles: boolean,
): GlobalDropSession {
  return { depth: session.depth + 1, containsFiles: session.containsFiles || containsFiles };
}

export function dropSessionAfterOver(
  session: GlobalDropSession,
  containsFiles: boolean,
): GlobalDropSession {
  if (session.containsFiles || !containsFiles) return session;
  return { ...session, containsFiles: true };
}

export function dropSessionAfterLeave(session: GlobalDropSession): GlobalDropSession {
  return { ...session, depth: Math.max(0, session.depth - 1) };
}

export function globalDropOverlayVisible(session: GlobalDropSession): boolean {
  return session.depth > 0 && session.containsFiles;
}

export function GlobalDropIndicator({ leaving = false }: { leaving?: boolean } = {}) {
  return (
    <div className={`global-drop-indicator${leaving ? " is-leaving" : ""}`} aria-hidden="true">
      <div className="global-drop-frame">
        <div className="global-drop-copy">
          <p className="section-label">Study Forge</p>
          <p className="global-drop-heading">Drop to load source</p>
          <p className="global-drop-hint">One PDF, or an ordered image group</p>
        </div>
      </div>
    </div>
  );
}

export function useGlobalFileDrop({ disabled, onFilesDropped }: UseGlobalFileDropOptions): boolean {
  const [overlayVisible, setOverlayVisible] = useState(false);
  const sessionRef = useRef<GlobalDropSession>(beginGlobalDropSession());
  const onFilesDroppedRef = useRef(onFilesDropped);
  const disabledRef = useRef(disabled);

  useEffect(() => {
    onFilesDroppedRef.current = onFilesDropped;
  }, [onFilesDropped]);

  useEffect(() => {
    disabledRef.current = disabled;
  }, [disabled]);

  useEffect(() => {
    const syncOverlay = () => {
      setOverlayVisible(globalDropOverlayVisible(sessionRef.current));
    };
    const resetSession = () => {
      sessionRef.current = beginGlobalDropSession();
      syncOverlay();
    };

    const handleDragEnter = (event: DragEvent) => {
      sessionRef.current = dropSessionAfterEnter(
        sessionRef.current,
        sourceDropHasFiles(event.dataTransfer, inAppDragInProgress()),
      );
      syncOverlay();
    };

    const handleDragOver = (event: DragEvent) => {
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      // Allow the drop anywhere in the window instead of navigating to the file.
      // In-app drags are refused the same way: they must not navigate either.
      event.preventDefault();
      const sourceDrop = !inAppDragInProgress();
      if (event.dataTransfer !== null) {
        event.dataTransfer.dropEffect = sourceDrop ? "copy" : "none";
      }
      if (!sourceDrop) return;
      sessionRef.current = dropSessionAfterOver(sessionRef.current, true);
      syncOverlay();
    };

    const handleDragLeave = (event: DragEvent) => {
      // When the pointer leaves the window entirely, no element is the new target.
      if (event.relatedTarget === null) {
        resetSession();
        return;
      }
      sessionRef.current = dropSessionAfterLeave(sessionRef.current);
      syncOverlay();
    };

    const handleDrop = (event: DragEvent) => {
      if (!dataTransferHasFiles(event.dataTransfer)) {
        resetSession();
        return;
      }
      // Never let a dropped in-app image navigate the window to its preview URL.
      event.preventDefault();
      const files = event.dataTransfer === null ? [] : Array.from(event.dataTransfer.files);
      const sourceDrop = !inAppDragInProgress();
      resetSession();
      if (disabledRef.current || !sourceDrop) return;
      onFilesDroppedRef.current(files);
    };

    const stopTrackingInAppDrags = trackInAppDrags();
    window.addEventListener("dragenter", handleDragEnter);
    window.addEventListener("dragover", handleDragOver);
    window.addEventListener("dragleave", handleDragLeave);
    window.addEventListener("drop", handleDrop);
    return () => {
      stopTrackingInAppDrags();
      window.removeEventListener("dragenter", handleDragEnter);
      window.removeEventListener("dragover", handleDragOver);
      window.removeEventListener("dragleave", handleDragLeave);
      window.removeEventListener("drop", handleDrop);
    };
  }, []);

  return overlayVisible;
}
