import { memo, useState, type KeyboardEvent } from "react";

import type { GuideSummary, HistoryEntry } from "../api";

export interface HistoryListProps {
  guides: HistoryEntry[];
  activeGuideId?: string | null;
  disabled?: boolean;
  onOpen: (guide: GuideSummary) => void | Promise<void>;
  onRename: (guideId: string, name: string) => void | Promise<void>;
  onDelete: (guideId: string) => void | Promise<void>;
  onRetry: (guideId: string) => void | Promise<void>;
}

export function sortHistoryNewestFirst(guides: HistoryEntry[]): HistoryEntry[] {
  return [...guides].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function resolveSubmittedGuideName(previousName: string, submittedName: string): string {
  const nextName = submittedName.trim();
  return nextName.length > 0 ? nextName : previousName;
}

function selectionLabel(guide: GuideSummary): string {
  if (guide.source === null) return "Source unavailable";
  if (guide.selection.mode === "images") {
    const count = guide.source.image_count ?? guide.source.files.length;
    return `${count} ${count === 1 ? "image" : "images"}`;
  }
  if (guide.selection.mode === "custom") {
    return `Pages ${guide.selection.start}–${guide.selection.end}`;
  }
  return "Entire document";
}

function statusLabel(status: string): string {
  if (status === "ok") return "Ready";
  if (status === "failed") return "Failed";
  if (status === "needs-attention") return "Needs attention";
  if (status === "running") return "Generating";
  if (status === "pending") return "Queued";
  return status;
}

function isFailed(status: string): boolean {
  return status === "failed" || status === "needs-attention";
}

function statusTone(status: string): string {
  if (status === "ok") return "success";
  if (isFailed(status)) return "failure";
  if (status === "pending") return "pending";
  return "working";
}

export const HistoryList = memo(function HistoryList({
  guides,
  activeGuideId = null,
  disabled = false,
  onOpen,
  onRename,
  onDelete,
  onRetry,
}: HistoryListProps) {
  const [editingGuideId, setEditingGuideId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const orderedGuides = sortHistoryNewestFirst(guides);

  const beginRename = (guide: GuideSummary) => {
    setEditingGuideId(guide.guide_id);
    setDraftName(guide.name);
  };

  const cancelRename = () => {
    setEditingGuideId(null);
    setDraftName("");
  };

  const finishRename = (guide: GuideSummary) => {
    if (editingGuideId !== guide.guide_id) return;
    const nextName = resolveSubmittedGuideName(guide.name, draftName);
    cancelRename();
    if (nextName !== guide.name) {
      void onRename(guide.guide_id, nextName);
    }
  };

  const handleRenameKey = (event: KeyboardEvent<HTMLInputElement>, guide: GuideSummary) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finishRename(guide);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  };

  return (
    <section className="history-section" aria-labelledby="history-heading">
      <div className="section-heading">
        <p className="section-label" id="history-heading">History</p>
        {orderedGuides.length > 0 ? <span className="section-count">{orderedGuides.length}</span> : null}
      </div>

      {orderedGuides.length === 0 ? (
        <p className="history-empty">Saved guides will appear here.</p>
      ) : (
        <ol className="history-list">
          {orderedGuides.map((guide) => {
            if (guide.kind === "legacy") {
              return (
                <li className="history-item history-item-legacy" key={guide.history_id}>
                  <div className="history-item-main">
                    <strong className="history-item-name">{guide.name}</strong>
                    <span className="history-item-source">Older Study Forge data</span>
                  </div>
                  <p className="history-item-notice" role="note">{guide.message}</p>
                </li>
              );
            }

            const failed = isFailed(guide.status);
            const editing = editingGuideId === guide.guide_id;
            return (
              <li
                className={`history-item${activeGuideId === guide.guide_id ? " is-active" : ""}`}
                key={guide.guide_id}
                data-guide-id={guide.guide_id}
              >
                <div className="history-item-main">
                  {editing ? (
                    <input
                      className="history-name-input"
                      value={draftName}
                      onChange={(event) => setDraftName(event.target.value)}
                      onBlur={() => finishRename(guide)}
                      onKeyDown={(event) => handleRenameKey(event, guide)}
                      aria-label={`Rename ${guide.name}`}
                      autoFocus
                    />
                  ) : (
                    <strong className="history-item-name">{guide.name}</strong>
                  )}
                  <span className="history-item-source">
                    {guide.source?.display_name ?? "Source unavailable"}
                  </span>
                  <span className="history-item-selection">{selectionLabel(guide)}</span>
                </div>

                <div className={`history-status status-${statusTone(guide.status)}`}>
                  <span className="status-dot" aria-hidden="true" />
                  <span>{statusLabel(guide.status)}</span>
                </div>

                {guide.error ? <p className="history-item-error">{guide.error}</p> : null}
                {guide.source_error ? (
                  <p className="history-item-error" role="alert">{guide.source_error}</p>
                ) : null}

                <div className="history-actions">
                  {failed ? (
                    <>
                      <button
                        type="button"
                        className="history-action history-action-primary"
                        onClick={() => void onRetry(guide.guide_id)}
                        disabled={disabled}
                      >
                        Retry
                      </button>
                      <button
                        type="button"
                        className="history-action"
                        onClick={() => void onDelete(guide.guide_id)}
                        disabled={disabled}
                      >
                        Delete
                      </button>
                    </>
                  ) : guide.status === "ok" ? (
                    <>
                      <button
                        type="button"
                        className="history-action history-action-primary"
                        onClick={() => void onOpen(guide)}
                        disabled={disabled}
                      >
                        Open
                      </button>
                      {!editing ? (
                        <button
                          type="button"
                          className="history-action"
                          onClick={() => beginRename(guide)}
                          disabled={disabled}
                        >
                          Rename
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="history-action"
                        onClick={() => void onDelete(guide.guide_id)}
                        disabled={disabled || editing}
                      >
                        Delete
                      </button>
                    </>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
});
