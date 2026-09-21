import { useEffect, useMemo, useRef, useState } from "react";

import type { QueueRow } from "../api";
import { useLeavingValue } from "../useLeavingValue";

export const QUIET_AFTER_MS = 20_000;
export const TICK_MS = 5_000;
export const QUEUE_PANEL_EXIT_MS = 160;
export const QUEUE_ROW_EXIT_MS = 160;

const ACTIVITY_LABELS: Record<string, string> = {
  preparing: "Preparing the source",
  waiting: "Waiting for the model",
  references: "Reading design references",
  writing: "Writing the guide",
  checking: "Checking the guide",
  correcting: "Correcting the guide",
  retry: "Waiting to retry",
};

const KIND_LABELS: Record<string, string> = {
  create: "New guide",
  retry: "Retry",
  clarify: "Clarification",
  update: "Update",
};

export interface GenerationQueueProps {
  rows: QueueRow[];
  connected: boolean;
  error?: string | null;
  onOpenGuide: (guideId: string) => void;
  onRetry: (row: QueueRow) => void;
  onDismiss: (receipt: string) => void;
  onDismissFinished?: () => void;
  now?: () => number;
  quietAfterMs?: number;
}

export function isActiveRow(row: QueueRow): boolean {
  return row.state === "running" || row.state === "waiting";
}

function stateRank(row: QueueRow): number {
  if (row.state === "running") return 0;
  if (row.state === "waiting") return 1;
  if (row.state === "failed" || row.state === "interrupted") return 2;
  return 3;
}

/** Active work first, then waiting work in accepted order, notices last. */
export function orderQueueRows(rows: QueueRow[]): QueueRow[] {
  return [...rows].sort((a, b) => {
    const ranked = stateRank(a) - stateRank(b);
    if (ranked !== 0) return ranked;
    return a.order - b.order || a.created_at.localeCompare(b.created_at);
  });
}

export function operationLabel(row: QueueRow): string {
  return KIND_LABELS[row.kind] ?? "Guide work";
}

export function activityLabel(row: QueueRow): string {
  if (row.activity === null || row.activity === undefined) {
    return row.state === "waiting" ? "Waiting to start" : "Working";
  }
  return ACTIVITY_LABELS[row.activity] ?? "Working";
}

function plural(value: number, one: string, many: string): string {
  return `${value} ${value === 1 ? one : many}`;
}

function secondsSince(timestamp: string | null, now: number): number | null {
  if (timestamp === null) return null;
  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, Math.round((now - parsed) / 1000));
}

/** What this request is doing right now, described from what actually arrived. */
export function progressDetail(
  row: QueueRow,
  now: number = Date.now(),
  quietAfterMs: number = QUIET_AFTER_MS,
): string {
  if (row.state === "waiting") return "Waiting to start";
  if (row.state === "completed") return "Finished";
  if (row.state === "interrupted") return "Stopped before it finished";
  if (row.state === "failed") return "Did not finish";
  if (row.state !== "running") return "Waiting to start";

  const attempt = row.attempt > 1 ? ` (attempt ${row.attempt})` : "";
  const label = `${activityLabel(row)}${attempt}`;
  const received = row.lines > 0 || row.characters > 0;
  if (!received) {
    const quiet = secondsSince(row.last_output_at, now);
    if (row.activity === "writing" && quiet !== null) {
      return `${label} · no new lines for ${quiet}s`;
    }
    return label;
  }
  const counted = `${plural(row.lines, "line", "lines")} · ${plural(
    row.characters,
    "character",
    "characters",
  )}`;
  const quiet = secondsSince(row.last_output_at, now);
  if (row.activity === "writing" && quiet !== null && quiet * 1000 >= quietAfterMs) {
    return `${label} · ${counted} · no new lines for ${quiet}s`;
  }
  return `${label} · ${counted}`;
}

export function queueSummaryText(rows: QueueRow[], connected = true): string {
  if (!connected) return "Not connected · work may still be running";
  const running = rows.filter((row) => row.state === "running").length;
  const waiting = rows.filter((row) => row.state === "waiting").length;
  const failed = rows.filter(
    (row) => row.state === "failed" || row.state === "interrupted",
  ).length;
  const ready = rows.filter((row) => row.state === "completed").length;
  const parts: string[] = [];
  if (running > 0) parts.push(`${running} generating`);
  if (waiting > 0) parts.push(`${waiting} waiting`);
  if (parts.length === 0 && failed > 0) {
    parts.push(plural(failed, "request failed", "requests failed"));
  }
  if (parts.length === 0 && ready > 0) {
    parts.push(ready === 1 ? "1 guide ready" : `${ready} guides ready`);
  }
  return parts.length > 0 ? parts.join(" · ") : "Queue is empty";
}

export function GenerationQueue({
  rows,
  connected,
  error,
  onOpenGuide,
  onRetry,
  onDismiss,
  onDismissFinished,
  now,
  quietAfterMs,
}: GenerationQueueProps) {
  const [expanded, setExpanded] = useState(false);
  const [collapsedByUser, setCollapsedByUser] = useState(false);
  const [, setTick] = useState(0);
  const [retiredRows, setRetiredRows] = useState<Map<string, QueueRow>>(new Map());
  const previousRowsRef = useRef<Map<string, QueueRow>>(new Map());

  useEffect(() => {
    const currentReceipts = new Set(rows.map((row) => row.receipt));
    const newlyRetired: QueueRow[] = [];
    for (const [receipt, prevRow] of previousRowsRef.current.entries()) {
      if (!currentReceipts.has(receipt)) {
        newlyRetired.push(prevRow);
      }
    }
    const nextMap = new Map<string, QueueRow>();
    for (const row of rows) {
      nextMap.set(row.receipt, row);
    }
    previousRowsRef.current = nextMap;

    if (newlyRetired.length > 0) {
      setRetiredRows((prev) => {
        const next = new Map(prev);
        newlyRetired.forEach((row) => next.set(row.receipt, row));
        return next;
      });
      const timer = setTimeout(() => {
        setRetiredRows((prev) => {
          const next = new Map(prev);
          newlyRetired.forEach((row) => next.delete(row.receipt));
          return next;
        });
      }, QUEUE_ROW_EXIT_MS);
      return () => clearTimeout(timer);
    }
  }, [rows]);

  const queueSurface = useLeavingValue(
    rows.length > 0 || retiredRows.size > 0 ? rows : null,
    QUEUE_PANEL_EXIT_MS,
  );
  const activeRows = rows.length > 0 ? rows : (queueSurface.shown ?? []);
  const allRows = useMemo(() => {
    const combined = [...activeRows];
    for (const retired of retiredRows.values()) {
      if (!combined.some((row) => row.receipt === retired.receipt)) {
        combined.push(retired);
      }
    }
    return combined;
  }, [activeRows, retiredRows]);
  const ordered = useMemo(() => orderQueueRows(allRows), [allRows]);
  const running = ordered.some((row) => row.state === "running");

  useEffect(() => {
    if (ordered.length > 0 && !collapsedByUser) {
      setExpanded(true);
    }
  }, [ordered.length, collapsedByUser]);

  useEffect(() => {
    if (!running) return;
    // Only the elapsed-time wording needs refreshing between queue updates.
    const timer = window.setInterval(() => setTick((value) => value + 1), TICK_MS);
    return () => window.clearInterval(timer);
  }, [running]);

  if (queueSurface.shown === null) return null;

  const current = now ? now() : Date.now();
  const summaryRows = rows.length > 0 ? rows : (queueSurface.shown ?? []);
  const summary = queueSummaryText(orderQueueRows(summaryRows), connected);
  const announcement = summaryRows
    .filter((row) => row.state === "running")
    .map((row) => `${row.guide_name}: ${activityLabel(row)}`)
    .join(", ");
  const notices = summaryRows.filter((row) => row.state === "completed");

  return (
    <section
      className={`generation-queue${queueSurface.leaving ? " is-leaving" : ""}`}
      aria-label="Guide queue"
      aria-hidden={queueSurface.leaving ? true : undefined}
    >
      <div className="generation-queue-bar">
        <button
          type="button"
          className="generation-queue-toggle"
          aria-expanded={expanded}
          aria-controls="generation-queue-list"
          onClick={() => {
            setExpanded(!expanded);
            setCollapsedByUser(expanded);
          }}
        >
          <span className="generation-queue-summary">{summary}</span>
          <span className="generation-queue-chevron" aria-hidden="true">
            {expanded ? "\u25be" : "\u25b8"}
          </span>
        </button>
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {announcement ? `${summary}. ${announcement}` : summary}
      </p>
      {expanded ? (
        <div className="generation-queue-body">
          {connected ? null : (
            <p className="generation-queue-connection" role="alert">
              {error ?? "The study service could not be reached."} Last known rows are shown, so
              guides that were working stay locked.
            </p>
          )}
          <ul className="generation-queue-list" id="generation-queue-list">
            {ordered.map((row) => {
              const isLeaving = retiredRows.has(row.receipt);
              return (
                <li
                  className={`generation-queue-row is-${row.state}${isLeaving ? " is-leaving" : ""}`}
                  key={row.receipt}
                >
                  <p className="generation-queue-head">
                    <span className="generation-queue-name" title={row.guide_name}>
                      {row.guide_name}
                    </span>
                    <span className="generation-queue-kind">{operationLabel(row)}</span>
                  </p>
                  <p className="generation-queue-detail">
                    {progressDetail(row, current, quietAfterMs)}
                  </p>
                  {row.error ? (
                    <p className="generation-queue-error">{row.error}</p>
                  ) : null}
                  <div className="generation-queue-actions">
                    <button
                      type="button"
                      className="generation-queue-action"
                      onClick={() => onOpenGuide(row.guide_id)}
                      disabled={isLeaving}
                    >
                      Open
                    </button>
                    {row.retry_available ? (
                      <button
                        type="button"
                        className="generation-queue-action"
                        onClick={() => onRetry(row)}
                        disabled={isLeaving}
                      >
                        Retry
                      </button>
                    ) : null}
                    {isActiveRow(row) ? null : (
                      <button
                        type="button"
                        className="generation-queue-action"
                        onClick={() => onDismiss(row.receipt)}
                        disabled={isLeaving}
                      >
                        Dismiss
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {notices.length > 0 && onDismissFinished ? (
            <div className="generation-queue-footer">
              <button
                type="button"
                className="generation-queue-action"
                onClick={onDismissFinished}
              >
                Clear finished notices
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
