import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  Api,
  GuideSelection,
  GuideView,
  QueueRow,
  QueueSummary,
  RevisionRequest,
} from "./api";
import { ApiError } from "./api";
import { traceElapsedMs, traceLog, traceNow } from "./traceLog";

export const ACTIVE_REFRESH_MS = 500;
export const IDLE_REFRESH_MS = 2000;
export const ACTIVE_STATES: ReadonlyArray<string> = ["waiting", "running"];
export const CLOSING_MESSAGE =
  "Study Forge is closing, so this request was not sent. Reopen the app to try again.";

export interface SubmissionOutcome {
  guide_id: string;
  /** The service's accepted view, or null when the reply was lost but found again. */
  guide: GuideView | null;
  operation: QueueRow | null;
}

export interface UseGenerationQueueOptions {
  /** Null while the local service is still starting. */
  api: Api | null;
  enabled?: boolean;
  activeIntervalMs?: number;
  idleIntervalMs?: number;
  onGuideSettled?: (guideId: string) => void;
}

export interface GenerationQueue {
  rows: QueueRow[];
  busyGuides: ReadonlySet<string>;
  finished: QueueRow[];
  connected: boolean;
  error: string | null;
  submitting: boolean;
  submittingGuides: ReadonlySet<string>;
  dismiss: (receipt: string) => void;
  dismissFinished: () => void;
  submitGuide: (sourceId: string, selection: GuideSelection) => Promise<SubmissionOutcome>;
  generate: (guideId: string) => Promise<SubmissionOutcome>;
  retryGuide: (guideId: string) => Promise<SubmissionOutcome>;
  revise: (guideId: string, request: RevisionRequest) => Promise<SubmissionOutcome>;
  retryOperation: (receipt: string) => Promise<SubmissionOutcome>;
  refresh: () => Promise<void>;
}

export function newReceipt(): string {
  const values = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(values);
  } else {
    for (let index = 0; index < values.length; index += 1) {
      values[index] = Math.floor(Math.random() * 256);
    }
  }
  return Array.from(values, (value) => value.toString(16).padStart(2, "0")).join("");
}

function isActive(row: QueueRow): boolean {
  return ACTIVE_STATES.includes(row.state);
}

function order(a: QueueRow, b: QueueRow): number {
  return a.order - b.order || a.created_at.localeCompare(b.created_at);
}

function merge(snapshot: QueueRow[], overrides: Map<string, QueueRow>): QueueRow[] {
  const byReceipt = new Map(snapshot.map((row) => [row.receipt, row]));
  for (const [receipt, row] of overrides) {
    if (!byReceipt.has(receipt)) byReceipt.set(receipt, row);
  }
  return [...byReceipt.values()].sort(order);
}

/**
 * Follow the guide queue without owning the selected guide or the source draft.
 *
 * One refresh runs at a time, faster while work is moving and slower when the
 * queue is idle. Snapshots that are older than what is already shown, or that
 * come from a service that has been replaced, are ignored, so a slow reply can
 * neither rewind the panel nor unlock a guide that is still busy.
 */
export function useGenerationQueue(options: UseGenerationQueueOptions): GenerationQueue {
  const {
    api,
    enabled: enabledOption,
    activeIntervalMs = ACTIVE_REFRESH_MS,
    idleIntervalMs = IDLE_REFRESH_MS,
    onGuideSettled,
  } = options;
  const enabled = enabledOption ?? api !== null;

  const [rows, setRows] = useState<QueueRow[]>([]);
  const [finished, setFinished] = useState<QueueRow[]>([]);
  const [connected, setConnected] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submittingGuides, setSubmittingGuides] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [localBusy, setLocalBusy] = useState<ReadonlySet<string>>(() => new Set());

  const snapshotRef = useRef<QueueRow[]>([]);
  const overridesRef = useRef(new Map<string, QueueRow>());
  const seenActiveRef = useRef(new Set<string>());
  const noticedRef = useRef(new Set<string>());
  const dismissedRef = useRef(new Set<string>());
  const localBusyRef = useRef(new Set<string>());
  const receiptsRef = useRef(new Map<string, string>());
  const serviceStartRef = useRef<string | null>(null);
  const changeRef = useRef(-1);
  const activeRef = useRef(false);
  const inFlightRef = useRef(false);
  const runningRef = useRef(0);
  const mountedRef = useRef(true);
  const settledRef = useRef(onGuideSettled);
  settledRef.current = onGuideSettled;

  const publish = useCallback(() => {
    const merged = merge(snapshotRef.current, overridesRef.current).filter(
      (row) => !dismissedRef.current.has(row.receipt),
    );
    setRows(merged);
  }, []);

  const applySnapshot = useCallback(
    (snapshot: QueueSummary) => {
      const replaced = serviceStartRef.current !== snapshot.service_start;
      if (replaced) {
        // A different service means earlier rows belong to an app session that
        // has ended: keep unfinished and interrupted work, but no old notices.
        serviceStartRef.current = snapshot.service_start;
        changeRef.current = -1;
        overridesRef.current.clear();
        seenActiveRef.current.clear();
        noticedRef.current.clear();
        dismissedRef.current.clear();
        setFinished([]);
      } else if (snapshot.change_number < changeRef.current) {
        return;
      }
      changeRef.current = snapshot.change_number;
      snapshotRef.current = snapshot.operations;
      for (const row of snapshot.operations) {
        // The service is authoritative for a request it has now reported.
        overridesRef.current.delete(row.receipt);
        if (isActive(row)) {
          seenActiveRef.current.add(row.receipt);
        } else if (
          seenActiveRef.current.has(row.receipt) &&
          !noticedRef.current.has(row.receipt)
        ) {
          // This request was seen moving and has now stopped: report it once.
          noticedRef.current.add(row.receipt);
          if (row.state === "completed") {
            setFinished((current) =>
              current.some((notice) => notice.receipt === row.receipt)
                ? current
                : [...current, row].sort(order),
            );
          }
          settledRef.current?.(row.guide_id);
        }
      }
      const activeGuides = new Set(
        snapshot.operations.filter(isActive).map((row) => row.guide_id),
      );
      let dropped = false;
      for (const guideId of [...localBusyRef.current]) {
        if (!activeGuides.has(guideId)) {
          localBusyRef.current.delete(guideId);
          dropped = true;
        }
      }
      if (dropped) {
        setLocalBusy(new Set(localBusyRef.current));
      }
      activeRef.current = snapshot.operations.some(isActive);
      publish();
    },
    [publish],
  );

  const refresh = useCallback(async (): Promise<void> => {
    if (api === null || inFlightRef.current) return;
    inFlightRef.current = true;
    runningRef.current += 1;
    try {
      const snapshot = await api.getQueue();
      if (!mountedRef.current) return;
      applySnapshot(snapshot);
      setConnected(true);
      setError(null);
    } catch (caught) {
      if (!mountedRef.current) return;
      // Keep the last known rows: an unreachable service must not unlock a guide.
      setConnected(false);
      setError(caught instanceof Error ? caught.message : "the service could not be reached");
    } finally {
      inFlightRef.current = false;
      runningRef.current -= 1;
    }
  }, [api, applySnapshot]);

  const scheduleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const schedule = useCallback(() => {
    if (!enabled || !mountedRef.current) return;
    if (scheduleRef.current !== null) {
      clearTimeout(scheduleRef.current);
    }
    const delay = activeRef.current ? activeIntervalMs : idleIntervalMs;
    scheduleRef.current = setTimeout(() => {
      scheduleRef.current = null;
      void (async () => {
        await refresh();
        schedule();
      })();
    }, delay);
  }, [enabled, activeIntervalMs, idleIntervalMs, refresh]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) {
      return () => {
        mountedRef.current = false;
      };
    }
    let cancelled = false;
    void (async () => {
      await refresh();
      if (!cancelled) schedule();
    })();
    return () => {
      cancelled = true;
      mountedRef.current = false;
      if (scheduleRef.current !== null) {
        clearTimeout(scheduleRef.current);
        scheduleRef.current = null;
      }
    };
  }, [enabled, refresh, schedule]);

  const acceptLocally = useCallback(
    (row: QueueRow) => {
      overridesRef.current.set(row.receipt, row);
      if (isActive(row)) {
        seenActiveRef.current.add(row.receipt);
      }
      // Anything the service might still send for this acceptance is now older.
      changeRef.current = Math.max(changeRef.current, 0) + 1;
      activeRef.current = activeRef.current || isActive(row);
      publish();
    },
    [publish],
  );

  const addSubmitting = useCallback((guideId: string | null) => {
    if (guideId === null) {
      return;
    }
    setSubmittingGuides((current) => new Set([...current, guideId]));
  }, []);

  const removeSubmitting = useCallback((guideId: string | null) => {
    if (guideId === null) {
      return;
    }
    setSubmittingGuides((current) => {
      if (!current.has(guideId)) return current;
      const next = new Set(current);
      next.delete(guideId);
      return next;
    });
  }, []);

  const submit = useCallback(
    async (
      key: string,
      guideId: string | null,
      send: (service: Api, receipt: string) => Promise<GuideView>,
    ): Promise<SubmissionOutcome> => {
      if (api === null) {
        throw new Error("The study service is not ready.");
      }
      const service = api;
      const request = receiptsRef.current.get(key) ?? newReceipt();
      receiptsRef.current.set(key, request);
      const traceStartedAt = traceNow();
      traceLog("submit.started", { key, receipt: request, guide_id: guideId });
      // The desktop refuses this handshake once a quit decision has started, so
      // a request can never be sent into a service that is already stopping.
      const bridge = typeof window === "undefined" ? undefined : window.studyForge;
      let handshake: number | null = null;
      if (bridge?.beginSubmission) {
        try {
          handshake = await bridge.beginSubmission();
        } catch {
          handshake = null;
        }
        if (handshake === null) {
          receiptsRef.current.delete(key);
          throw new Error(CLOSING_MESSAGE);
        }
      }
      addSubmitting(guideId);
      try {
        let guide: GuideView;
        try {
          guide = await send(service, request);
          traceLog("submit.accepted", {
            key,
            receipt: request,
            guide_id: guide.guide_id,
            status: guide.status,
            duration_ms: traceElapsedMs(traceStartedAt),
          });
        } catch (caught) {
          if (caught instanceof ApiError) {
            // The service answered, so this receipt is resolved either way.
            receiptsRef.current.delete(key);
            traceLog("submit.refused", {
              key,
              receipt: request,
              status: caught.status,
              error: caught.message,
              duration_ms: traceElapsedMs(traceStartedAt),
            });
            throw caught;
          }
          // The reply was lost. Find out whether the service recorded the request.
          try {
            const row = await service.getOperation(request);
            receiptsRef.current.delete(key);
            acceptLocally(row);
            traceLog("submit.recovered", {
              key,
              receipt: request,
              guide_id: row.guide_id,
              state: row.state,
              duration_ms: traceElapsedMs(traceStartedAt),
            });
            return { guide_id: row.guide_id, guide: null, operation: row };
          } catch (lookup) {
            if (lookup instanceof ApiError && lookup.status === 404) {
              // Confirmed absent: repeating the same receipt is allowed.
              throw caught;
            }
            setConnected(false);
            setError(
              caught instanceof Error ? caught.message : "the submission could not be confirmed",
            );
            traceLog("submit.unconfirmed", {
              key,
              receipt: request,
              error: caught instanceof Error ? caught.message : "the submission failed",
              duration_ms: traceElapsedMs(traceStartedAt),
            });
            throw caught;
          }
        }
        receiptsRef.current.delete(key);
        const operation = (guide.operation ?? null) as QueueRow | null;
        if (operation !== null) {
          acceptLocally(operation);
        } else if (guide.status !== "ok") {
          localBusyRef.current.add(guide.guide_id);
          setLocalBusy(new Set(localBusyRef.current));
        }
        return { guide_id: guide.guide_id, guide, operation };
      } finally {
        removeSubmitting(guideId);
        if (handshake !== null) {
          void bridge?.endSubmission?.(handshake);
        }
      }
    },
    [acceptLocally, addSubmitting, api, removeSubmitting],
  );

  const submitGuide = useCallback(
    (sourceId: string, selection: GuideSelection) =>
      submit(`create:${sourceId}:${JSON.stringify(selection)}`, null, (service, request) =>
        service.createGuide(sourceId, selection, request),
      ),
    [submit],
  );

  const generate = useCallback(
    (guideId: string) =>
      submit(`generate:${guideId}`, guideId, (service, request) =>
        service.generateGuide(guideId, request),
      ),
    [submit],
  );

  const retryGuide = useCallback(
    (guideId: string) =>
      submit(`retry:${guideId}`, guideId, (service, request) =>
        service.retryGuide(guideId, request),
      ),
    [submit],
  );

  const revise = useCallback(
    (guideId: string, request: RevisionRequest) =>
      submit(
        `revise:${guideId}:${JSON.stringify(request)}`,
        guideId,
        (service, receipt) => service.reviseGuide(guideId, request, receipt),
      ),
    [submit],
  );

  const retryOperation = useCallback(
    (receipt: string) =>
      submit(`request:${receipt}`, null, (service, next) =>
        service.retryOperation(receipt, next),
      ),
    [submit],
  );

  const dismiss = useCallback(
    (receipt: string) => {
      dismissedRef.current.add(receipt);
      setFinished((current) => current.filter((notice) => notice.receipt !== receipt));
      publish();
    },
    [publish],
  );

  const dismissFinished = useCallback(() => {
    setFinished((current) => {
      for (const notice of current) {
        dismissedRef.current.add(notice.receipt);
      }
      return [];
    });
    publish();
  }, [publish]);

  const busyGuides = useMemo(() => {
    const busy = new Set<string>();
    for (const row of rows) {
      if (isActive(row)) busy.add(row.guide_id);
    }
    for (const guideId of localBusy) busy.add(guideId);
    return busy;
  }, [rows, localBusy]);

  return {
    rows,
    busyGuides,
    finished,
    connected,
    error,
    submitting: submittingGuides.size > 0,
    submittingGuides,
    dismiss,
    dismissFinished,
    submitGuide,
    generate,
    retryGuide,
    revise,
    retryOperation,
    refresh,
  };
}
