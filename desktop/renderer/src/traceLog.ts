/**
 * Client-side timing notes for the generation trace.
 *
 * The backend writes one JSONL trace per request receipt (`jobs/logs/
 * generation-<receipt>.jsonl`). These console entries carry the same receipt,
 * so the two halves of one click-to-guide timeline line up: when the button was
 * pressed, when the service accepted the request, and when the first queue row
 * for it appeared.
 *
 * Every entry is prefixed so it can be filtered in the dev console
 * (`[forge-trace]`), and logging never throws: a missing console is not a
 * reason for a guide to fail.
 */

export const TRACE_PREFIX = "[forge-trace]";

export function traceLog(scope: string, fields: Record<string, unknown> = {}): void {
  const info = globalThis.console?.info;
  if (typeof info !== "function") return;
  try {
    info.call(globalThis.console, `${TRACE_PREFIX} ${scope}`, fields);
  } catch {
    // A console that refuses output must not disturb the request it describes.
  }
}

export function traceNow(): number {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

export function traceElapsedMs(startedAt: number): number {
  return Math.round((traceNow() - startedAt) * 10) / 10;
}
