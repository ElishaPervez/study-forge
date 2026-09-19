import { EventEmitter } from "node:events";

import { describe, expect, it, vi } from "vitest";

import { ANNOUNCE_PREFIX, parseAnnouncedPort, waitForPort } from "./handshake";

class FakeProcess extends EventEmitter {
  readonly stdout = new EventEmitter();

  writeStdout(chunk: string): void {
    this.stdout.emit("data", Buffer.from(chunk, "utf8"));
  }
}

describe("parseAnnouncedPort", () => {
  it("reads the announced port", () => {
    expect(parseAnnouncedPort(`${ANNOUNCE_PREFIX}53124`)).toBe(53124);
  });

  it("ignores unrelated log lines", () => {
    expect(parseAnnouncedPort("INFO: Application startup complete.")).toBeNull();
  });

  it("rejects out-of-range and non-numeric ports", () => {
    expect(parseAnnouncedPort(`${ANNOUNCE_PREFIX}0`)).toBeNull();
    expect(parseAnnouncedPort(`${ANNOUNCE_PREFIX}70000`)).toBeNull();
    expect(parseAnnouncedPort(`${ANNOUNCE_PREFIX}abc`)).toBeNull();
  });

  it("does not accept a port embedded in other text", () => {
    expect(parseAnnouncedPort(`noise ${ANNOUNCE_PREFIX}8080`)).toBeNull();
  });
});

describe("waitForPort", () => {
  it("waits for a complete announced line across stdout chunks", async () => {
    const child = new FakeProcess();
    const result = waitForPort(child, 1000);

    child.writeStdout("INFO: starting backend\nLESSON_GEN_");
    child.writeStdout("PORT=53124\n");

    await expect(result).resolves.toBe(53124);
  });

  it("rejects when the backend emits an error before announcing", async () => {
    const child = new FakeProcess();
    const result = waitForPort(child, 1000);
    const error = new Error("spawn failed");

    child.emit("error", error);

    await expect(result).rejects.toBe(error);
  });

  it("rejects when the backend exits before announcing", async () => {
    const child = new FakeProcess();
    const result = waitForPort(child, 1000);

    child.emit("exit", 7, null);

    await expect(result).rejects.toThrow("backend exited before announcing a port (7)");
  });

  it("rejects on timeout and removes all listeners and timers", async () => {
    vi.useFakeTimers();
    try {
      const child = new FakeProcess();
      const result = waitForPort(child, 2500);

      expect(child.listenerCount("error")).toBe(1);
      expect(child.listenerCount("exit")).toBe(1);
      expect(child.stdout.listenerCount("data")).toBe(1);

      vi.advanceTimersByTime(2500);

      await expect(result).rejects.toThrow("backend did not announce a port in 2500ms");
      expect(child.listenerCount("error")).toBe(0);
      expect(child.listenerCount("exit")).toBe(0);
      expect(child.stdout.listenerCount("data")).toBe(0);
      expect(vi.getTimerCount()).toBe(0);

      child.writeStdout(`${ANNOUNCE_PREFIX}53124\n`);
      child.emit("exit", 0, null);
      await expect(result).rejects.toThrow("backend did not announce a port in 2500ms");
    } finally {
      vi.useRealTimers();
    }
  });

  it("cleans up after resolving so later events cannot settle it again", async () => {
    const child = new FakeProcess();
    const result = waitForPort(child, 1000);

    child.writeStdout(`${ANNOUNCE_PREFIX}53124\n`);

    await expect(result).resolves.toBe(53124);
    expect(child.listenerCount("error")).toBe(0);
    expect(child.listenerCount("exit")).toBe(0);
    expect(child.stdout.listenerCount("data")).toBe(0);

    child.on("error", () => undefined);
    child.emit("error", new Error("late error"));
    child.emit("exit", 1, null);
  });
});
