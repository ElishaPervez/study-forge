import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";

import type { SourceKind, SourceView } from "../api";
import {
  ImageGroupEditor,
  moveImage,
  removeImage,
  type ImageFile,
} from "./ImageGroupEditor";

export { moveImage, removeImage } from "./ImageGroupEditor";

export interface SourceMetadata {
  imageCount?: number | null;
  totalBytes?: number | null;
}

export interface SourceDraft {
  kind: SourceKind;
  sourceId: string;
  displayName: string;
  metadata: SourceMetadata;
  imageFiles: ImageFile[];
  pageCount: number | null;
  paths: string[];
  registeredPaths?: string[];
  previewBaseUrl?: string;
  originalPathsAvailable?: boolean;
}

export interface SourceIntakeProps {
  source: SourceDraft | null;
  disabled?: boolean;
  busy?: boolean;
  error?: string | null;
  onPathsSelected: (paths: string[]) => void | Promise<void>;
  onRemove: () => void | Promise<void>;
  onImagesChange?: (files: ImageFile[]) => void;
}

const imageExtensions = new Set([".gif", ".jpeg", ".jpg", ".png", ".webp"]);

function extension(path: string): string {
  const fileName = path.split(/[\\/]/).pop() ?? path;
  const dot = fileName.lastIndexOf(".");
  return dot === -1 ? "" : fileName.slice(dot).toLowerCase();
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

export function classifySourcePaths(paths: string[]): { kind: SourceKind } | { error: string } {
  if (paths.length === 0) return { error: "Choose one PDF or one or more images." };

  const pdfCount = paths.filter((path) => extension(path) === ".pdf").length;
  const imageCount = paths.filter((path) => imageExtensions.has(extension(path))).length;

  if (pdfCount > 0 && imageCount > 0) {
    return { error: "PDF and image files cannot be mixed. Choose one PDF or an image group." };
  }
  if (pdfCount > 1) {
    return { error: "Choose one PDF, or choose one or more images." };
  }
  if (pdfCount === 1 && paths.length === 1) return { kind: "pdf" };
  if (imageCount === paths.length) return { kind: "images" };
  return { error: "Choose one PDF or one or more images." };
}

export function draftFromSource(source: SourceView, paths: string[]): SourceDraft {
  const imageFiles = source.kind === "images"
    ? paths.map((path) => ({ path, name: fileName(path) }))
    : [];
  return {
    kind: source.kind,
    sourceId: source.source_id,
    displayName: source.display_name,
    metadata: {
      imageCount: source.image_count,
      totalBytes: source.total_bytes,
    },
    imageFiles,
    pageCount: source.page_count,
    paths: [...paths],
    registeredPaths: [...paths],
    originalPathsAvailable: true,
  };
}

export function draftFromStoredSource(source: SourceView): SourceDraft {
  const imageFiles = source.kind === "images"
    ? source.files.map((storedName) => ({ name: storedName, storedName }))
    : [];
  return {
    kind: source.kind,
    sourceId: source.source_id,
    displayName: source.display_name,
    metadata: {
      imageCount: source.image_count,
      totalBytes: source.total_bytes,
    },
    imageFiles,
    pageCount: source.page_count,
    paths: [],
    registeredPaths: [],
    originalPathsAvailable: false,
  };
}

export function visibleSourceError(
  localError: string | null,
  parentError: string | null | undefined,
  localErrorParent: string | null | undefined = parentError,
): string | null {
  if (localError !== null && parentError === localErrorParent) return localError;
  return parentError ?? null;
}

export function beginSourceRemoval(
  clearLocalError: () => void,
  onRemove: () => void | Promise<void>,
): void {
  clearLocalError();
  void onRemove();
}

export function SourceIntake({
  source,
  disabled = false,
  busy = false,
  error,
  onPathsSelected,
  onRemove,
  onImagesChange,
}: SourceIntakeProps) {
  const [inputError, setInputError] = useState<string | null>(null);
  const localErrorParentRef = useRef<string | null | undefined>(error);
  const unavailable = disabled || busy;

  useEffect(() => {
    setInputError(null);
  }, [source?.sourceId]);

  useEffect(() => {
    if (error === localErrorParentRef.current) return;
    localErrorParentRef.current = error;
    setInputError(null);
  }, [error]);

  useEffect(() => {
    if (!busy) return;
    localErrorParentRef.current = error;
    setInputError(null);
  }, [busy, error]);

  const setLocalInputError = (message: string) => {
    localErrorParentRef.current = error;
    setInputError(message);
  };

  const acceptPaths = (paths: string[]) => {
    const result = classifySourcePaths(paths);
    if ("error" in result) {
      setLocalInputError(result.error);
      return;
    }
    setInputError(null);
    void onPathsSelected(paths);
  };

  const handlePick = async () => {
    if (unavailable || !window.lessonGen) return;
    try {
      const paths = await window.lessonGen.pickSourceFiles();
      if (paths.length > 0) acceptPaths(paths);
    } catch (pickError: unknown) {
      setLocalInputError(pickError instanceof Error ? pickError.message : "The source picker could not open.");
    }
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (unavailable || !window.lessonGen) return;
    const paths = Array.from(event.dataTransfer.files).map((file) => window.lessonGen?.pathForFile(file) ?? "");
    acceptPaths(paths.filter((path) => path.length > 0));
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
  };

  const handleInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const paths = Array.from(event.target.files ?? []).map((file) => window.lessonGen?.pathForFile(file) ?? "");
    acceptPaths(paths.filter((path) => path.length > 0));
    event.target.value = "";
  };

  const handleRemove = () => {
    if (unavailable) return;
    beginSourceRemoval(() => setInputError(null), onRemove);
  };

  const displayedError = busy
    ? error ?? null
    : visibleSourceError(inputError, error, localErrorParentRef.current);
  const storedSource = source?.originalPathsAvailable === false;

  return (
    <section className="rail-section source-section" aria-labelledby="source-heading">
      <div className="section-heading">
        <p className="section-label" id="source-heading">Source</p>
      </div>

      <div
        className={`source-picker${source ? " has-source" : ""}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        {source ? (
          <div className="source-file-row" title={source.paths.join("\n")}>
            <div className="file-icon" aria-hidden="true">{source.kind === "pdf" ? "PDF" : "IMG"}</div>
            <div className="source-file-copy">
              <strong>{source.displayName}</strong>
              <span>
                {source.kind === "pdf"
                  ? `${source.pageCount ?? "Unknown"} pages`
                  : `${source.imageFiles.length} ${source.imageFiles.length === 1 ? "image" : "images"}`}
              </span>
            </div>
          </div>
        ) : (
          <div className="source-prompt">
            <div className="file-icon file-icon-outline" aria-hidden="true">PDF<br />IMG</div>
            <div>
              <strong>Drop a PDF or image group</strong>
              <span>One PDF, or ordered images</span>
            </div>
          </div>
        )}

        {source ? (
          <button
            type="button"
            className="source-remove-action"
            onClick={handleRemove}
            disabled={unavailable || storedSource}
          >
            Remove
          </button>
        ) : null}

        <button type="button" className="secondary-button source-button" onClick={() => void handlePick()} disabled={unavailable}>
          {busy ? "Registering source..." : source ? "Choose different source" : "Choose source"}
        </button>
        <label className="source-file-input-label">
          <span>Browse files</span>
          <input
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.webp,.gif"
            onChange={handleInputChange}
            disabled={unavailable}
          />
        </label>
      </div>

      {displayedError ? <p className="source-inline-error" role="alert">{displayedError}</p> : null}

      {source?.kind === "images" && onImagesChange ? (
        <ImageGroupEditor
          files={source.imageFiles}
          disabled={unavailable || storedSource}
          onChange={onImagesChange}
        />
      ) : null}
    </section>
  );
}
