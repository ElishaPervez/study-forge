import { describe, expect, it, vi } from "vitest";

import { createCloseGuard, type CloseStatus, type CloseWarning } from "./closeGuard";

const IDLE: CloseStatus = { active: 0, waiting: 0, accepting: true, confirmed: false };

function status(partial: Partial<CloseStatus>): CloseStatus {
  return { ...IDLE, ...partial };
}

function harness(overrides: Partial<Parameters<typeof createCloseGuard>[0]> = {}) {
  const events: string[] = [];
  const warnings: CloseWarning[] = [];
  let pending = 0;
  let quitAnswer = true;

  const deps = {
    prepare: vi.fn(async () => {
      events.push("prepare");
      return IDLE;
    }),
    resume: vi.fn(async () => {
      events.push("resume");
      return status({ accepting: true });
    }),
    confirm: vi.fn(async () => {
      events.push("confirm");
      return status({ confirmed: true, accepting: false });
    }),
    warn: vi.fn(async (warning: CloseWarning) => {
      events.push("warn");
      warnings.push(warning);
      return quitAnswer;
    }),
    setSubmissionsFrozen: vi.fn((frozen: boolean) => {
      events.push(frozen ? "freeze" : "unfreeze");
    }),
    pendingSubmissions: vi.fn(() => pending),
    serviceMaybeRunning: vi.fn(() => true),
    stopBackend: vi.fn(async () => {
      events.push("stop");
      return true;
    }),
    reportStopFailure: vi.fn(() => {
      events.push("stop-failed");
    }),
    quit: vi.fn(() => {
      events.push("quit");
    }),
    ...overrides,
  };

  return {
    deps,
    events,
    warnings,
    setPending: (value: number) => {
      pending = value;
    },
    answer: (value: boolean) => {
      quitAnswer = value;
    },
    guard: createCloseGuard(deps),
  };
}

describe("close guard", () => {
  it("closes without a warning when nothing is running, waiting, or being submitted", async () => {
    const { guard, deps, events } = harness();

    await expect(guard.requestClose()).resolves.toBe(true);

    expect(deps.warn).not.toHaveBeenCalled();
    expect(deps.resume).not.toHaveBeenCalled();
    expect(events).toEqual(["freeze", "prepare", "confirm", "stop", "quit"]);
    expect(guard.approved).toBe(true);
  });

  it("keeps the app open and restores admission when the user chooses to stay", async () => {
    const { guard, deps, warnings, answer } = harness({
      prepare: vi.fn(async () => status({ active: 1 })),
    });
    answer(false);

    await expect(guard.requestClose()).resolves.toBe(false);

    expect(deps.resume).toHaveBeenCalledTimes(1);
    expect(deps.confirm).not.toHaveBeenCalled();
    expect(deps.stopBackend).not.toHaveBeenCalled();
    expect(deps.quit).not.toHaveBeenCalled();
    expect(deps.setSubmissionsFrozen).toHaveBeenLastCalledWith(false);
    expect(warnings).toHaveLength(1);
    expect(warnings[0].detail).toContain("1 guide being written");
    expect(warnings[0].uncertain).toBe(false);
    expect(guard.approved).toBe(false);
  });

  it("stops work when the user confirms quitting and never warns twice", async () => {
    const { guard, deps, warnings } = harness({
      prepare: vi.fn(async () => status({ active: 1, waiting: 2 })),
    });

    await expect(guard.requestClose()).resolves.toBe(true);
    expect(warnings[0].detail).toContain("1 guide being written, and 2 guides waiting to start");
    expect(deps.confirm).toHaveBeenCalledTimes(1);
    expect(deps.stopBackend).toHaveBeenCalledTimes(1);
    expect(deps.quit).toHaveBeenCalledTimes(1);

    // A second close attempt after confirmation is already approved: a late
    // completion cannot reopen the warning or restart the service lookups.
    await expect(guard.requestClose()).resolves.toBe(true);
    expect(warnings).toHaveLength(1);
    expect(deps.prepare).toHaveBeenCalledTimes(1);
  });

  it("warns about waiting-only work and counts it in the collapsed wording", async () => {
    const { guard, warnings, answer, deps } = harness({
      prepare: vi.fn(async () => status({ waiting: 2 })),
    });
    answer(false);

    await guard.requestClose();

    expect(warnings[0].detail).toContain("2 guides waiting to start");
    expect(warnings[0].detail).not.toContain("being written");
    expect(deps.resume).toHaveBeenCalledTimes(1);
  });

  it("warns about a submission paused before the service accepted it", async () => {
    const { guard, warnings, setPending, answer } = harness();
    setPending(1);
    answer(false);

    await guard.requestClose();

    expect(warnings).toHaveLength(1);
    expect(warnings[0].detail).toContain("1 submission not accepted yet");
    expect(warnings[0].uncertain).toBe(false);
  });

  it("asks once when two close attempts arrive at the same time", async () => {
    let releaseWarning: (value: boolean) => void = () => undefined;
    const warning = new Promise<boolean>((resolve) => {
      releaseWarning = resolve;
    });
    const { guard, deps } = harness({
      prepare: vi.fn(async () => status({ active: 1 })),
      warn: vi.fn(async () => warning),
    });

    const first = guard.requestClose();
    const second = guard.requestClose();

    await expect(second).resolves.toBe(false);
    expect(deps.warn).toHaveBeenCalledTimes(1);
    expect(guard.deciding).toBe(true);

    releaseWarning(false);
    await expect(first).resolves.toBe(false);
    expect(deps.resume).toHaveBeenCalledTimes(1);
    expect(guard.deciding).toBe(false);
  });

  it("shows an honest warning when the service cannot answer", async () => {
    const { guard, warnings, deps, answer } = harness({
      prepare: vi.fn(async () => {
        throw new Error("connect ECONNREFUSED");
      }),
    });
    answer(false);

    await expect(guard.requestClose()).resolves.toBe(false);

    expect(warnings).toHaveLength(1);
    expect(warnings[0].uncertain).toBe(true);
    expect(warnings[0].message).toContain("could not confirm whether work is still running");
    // Nothing to resume, because the service never answered.
    expect(deps.resume).not.toHaveBeenCalled();
  });

  it("does not treat a stopped connection as an empty queue", async () => {
    const { guard, deps, warnings, setPending, answer } = harness({
      prepare: vi.fn(async () => {
        throw new Error("socket closed");
      }),
      serviceMaybeRunning: vi.fn(() => true),
    });
    setPending(1);
    answer(false);

    await expect(guard.requestClose()).resolves.toBe(false);

    expect(warnings).toHaveLength(1);
    expect(warnings[0].detail).toContain("submission was still being sent");
    expect(deps.quit).not.toHaveBeenCalled();
  });

  it("closes quietly when the service never started and nothing was submitted", async () => {
    const { guard, deps } = harness({
      prepare: vi.fn(async () => {
        throw new Error("the study service is not running");
      }),
      serviceMaybeRunning: vi.fn(() => false),
    });

    await expect(guard.requestClose()).resolves.toBe(true);

    expect(deps.warn).not.toHaveBeenCalled();
    expect(deps.quit).toHaveBeenCalledTimes(1);
  });

  it("still stops work when the last operation finished while the warning was visible", async () => {
    let releaseWarning: (value: boolean) => void = () => undefined;
    const warning = new Promise<boolean>((resolve) => {
      releaseWarning = resolve;
    });
    const { guard, deps } = harness({
      prepare: vi.fn(async () => status({ active: 1 })),
      warn: vi.fn(async () => warning),
    });

    const attempt = guard.requestClose();
    await vi.waitFor(() => expect(deps.warn).toHaveBeenCalledTimes(1));
    // The worker finishes and the queue becomes idle while the dialog is open.
    releaseWarning(true);
    await expect(attempt).resolves.toBe(true);

    expect(deps.confirm).toHaveBeenCalledTimes(1);
    expect(deps.stopBackend).toHaveBeenCalledTimes(1);
    expect(deps.quit).toHaveBeenCalledTimes(1);
  });

  it("reports a backend stop failure and does not claim the work stopped", async () => {
    const { guard, deps, events } = harness({
      stopBackend: vi.fn(async () => {
        events.push("stop");
        return false;
      }),
    });

    await expect(guard.requestClose()).resolves.toBe(false);

    expect(deps.reportStopFailure).toHaveBeenCalledTimes(1);
    expect(deps.quit).not.toHaveBeenCalled();
    expect(guard.approved).toBe(false);
    expect(events).not.toContain("quit");
  });

  it("allows another close attempt after a failed process stop", async () => {
    const stopBackend = vi
      .fn<() => Promise<boolean>>()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const { guard, deps } = harness({ stopBackend });

    await expect(guard.requestClose()).resolves.toBe(false);
    await expect(guard.requestClose()).resolves.toBe(true);

    expect(deps.reportStopFailure).toHaveBeenCalledTimes(1);
    expect(deps.quit).toHaveBeenCalledTimes(1);
    expect(deps.setSubmissionsFrozen).toHaveBeenLastCalledWith(true);
  });
});
