/**
 * One close decision for every way the app can be closed.
 *
 * The guard freezes new submission handshakes, asks the study service to pause
 * admission, and warns the user before stopping any work. A warning is only
 * skipped when the service confirms there is nothing running, waiting, or
 * being submitted.
 */

export interface CloseStatus {
  active: number;
  waiting: number;
  accepting: boolean;
  confirmed: boolean;
}

export interface CloseWarning {
  message: string;
  detail: string;
  /** True when the service could not confirm whether work is still running. */
  uncertain: boolean;
}

export interface CloseGuardDeps {
  /** Ask the service to pause admission and queue advancement. */
  prepare: () => Promise<CloseStatus>;
  /** Restore admission after the user chooses to keep the app open. */
  resume: () => Promise<CloseStatus>;
  /** Record confirmed quit so nothing publishes afterwards. */
  confirm: () => Promise<CloseStatus>;
  /** Show the native warning. Resolves true only for "Quit and stop work". */
  warn: (warning: CloseWarning) => Promise<boolean>;
  /** Freeze (true) or reopen (false) submission handshakes. */
  setSubmissionsFrozen: (frozen: boolean) => void;
  /** Submissions that began a handshake and have not finished it yet. */
  pendingSubmissions: () => number;
  /** True when the study service process exists, so its silence is meaningful. */
  serviceMaybeRunning: () => boolean;
  /** Bounded process-tree shutdown for the local service. */
  stopBackend: () => Promise<boolean>;
  /** Report that the service could not be stopped; work is not claimed stopped. */
  reportStopFailure: () => void;
  /** Final, already-approved close. Must not reopen the warning. */
  quit: () => void;
}

export interface CloseGuard {
  /** Resolves true only when the app may close now. */
  requestClose: () => Promise<boolean>;
  /** True once a decision allowed closing. */
  readonly approved: boolean;
  /** True while a warning or service lookup is in progress. */
  readonly deciding: boolean;
}

function plural(count: number, noun: string): string {
  return count === 1 ? `1 ${noun}` : `${count} ${noun}s`;
}

function describe(
  status: CloseStatus | null,
  pending: number,
  reachable: boolean,
): CloseWarning {
  if (!reachable) {
    return {
      message: "Study Forge could not confirm whether work is still running.",
      detail:
        pending > 0
          ? "A submission was still being sent, and the study service did not answer. Quitting now may leave a guide or update unfinished. Unfinished work stays in History and can be retried after reopening."
          : "The study service did not answer, so queued or running work cannot be checked. Quitting now may leave a guide or update unfinished. Unfinished work stays in History and can be retried after reopening.",
      uncertain: true,
    };
  }

  const active = status?.active ?? 0;
  const waiting = status?.waiting ?? 0;
  const parts: string[] = [];
  if (active > 0) parts.push(`${plural(active, "guide")} being written`);
  if (waiting > 0) parts.push(`${plural(waiting, "guide")} waiting to start`);
  if (pending > 0) {
    parts.push(
      pending === 1
        ? "1 submission not accepted yet"
        : `${pending} submissions not accepted yet`,
    );
  }

  return {
    message: "Study Forge is still working.",
    detail: `${parts.join(", and ")}. Quitting stops this work. Unfinished guides stay in History and can be retried after reopening; completed guides are not affected.`,
    uncertain: false,
  };
}

export function createCloseGuard(deps: CloseGuardDeps): CloseGuard {
  let approved = false;
  let deciding = false;

  async function stopWork(): Promise<boolean> {
    try {
      await deps.confirm();
    } catch {
      // The service may already be gone; the process stop still has to run.
    }
    const stopped = await deps.stopBackend();
    if (!stopped) {
      deps.reportStopFailure();
      return false;
    }
    approved = true;
    deps.quit();
    return true;
  }

  async function requestClose(): Promise<boolean> {
    if (approved) return true;
    if (deciding) return false;
    deciding = true;
    deps.setSubmissionsFrozen(true);

    let status: CloseStatus | null = null;
    let reachable = true;
    try {
      status = await deps.prepare();
    } catch {
      reachable = false;
    }

    const pending = deps.pendingSubmissions();
    const hasWork = reachable
      ? (status?.active ?? 0) > 0 || (status?.waiting ?? 0) > 0 || pending > 0
      : pending > 0 || deps.serviceMaybeRunning();

    if (!hasWork) {
      deciding = false;
      return await stopWork();
    }

    const warning = describe(status, pending, reachable);
    let quitChosen = false;
    try {
      quitChosen = await deps.warn(warning);
    } catch {
      quitChosen = false;
    }

    if (!quitChosen) {
      if (reachable) {
        try {
          await deps.resume();
        } catch {
          // The service vanished while the warning was open; nothing to resume.
        }
      }
      deps.setSubmissionsFrozen(false);
      deciding = false;
      return false;
    }

    deciding = false;
    const stopped = await stopWork();
    if (!stopped) {
      // The app stays open, so submissions may start again; the service has
      // already recorded the interruption, so retrying close is still possible.
      deps.setSubmissionsFrozen(false);
    }
    return stopped;
  }

  return {
    requestClose,
    get approved() {
      return approved;
    },
    get deciding() {
      return deciding;
    },
  };
}
