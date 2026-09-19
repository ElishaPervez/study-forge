import { useEffect, useRef, useState } from "react";

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

export function GlobalDropIndicator() {
  return (
    <div className="global-drop-indicator" aria-hidden="true">
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
        dataTransferHasFiles(event.dataTransfer),
      );
      syncOverlay();
    };

    const handleDragOver = (event: DragEvent) => {
      if (dataTransferHasFiles(event.dataTransfer)) {
        // Allow the drop anywhere in the window instead of navigating to the file.
        event.preventDefault();
        if (event.dataTransfer !== null) {
          event.dataTransfer.dropEffect = "copy";
        }
        sessionRef.current = dropSessionAfterOver(sessionRef.current, true);
        syncOverlay();
      }
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
      event.preventDefault();
      const files = event.dataTransfer === null ? [] : Array.from(event.dataTransfer.files);
      resetSession();
      if (disabledRef.current) return;
      onFilesDroppedRef.current(files);
    };

    window.addEventListener("dragenter", handleDragEnter);
    window.addEventListener("dragover", handleDragOver);
    window.addEventListener("dragleave", handleDragLeave);
    window.addEventListener("drop", handleDrop);
    return () => {
      window.removeEventListener("dragenter", handleDragEnter);
      window.removeEventListener("dragover", handleDragOver);
      window.removeEventListener("dragleave", handleDragLeave);
      window.removeEventListener("drop", handleDrop);
    };
  }, []);

  return overlayVisible;
}
