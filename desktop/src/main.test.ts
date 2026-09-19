import { EventEmitter } from "node:events";
import type { BrowserWindow } from "electron";

import { beforeEach, describe, expect, it, vi } from "vitest";

const electronFakes = vi.hoisted(() => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>();
  class FakeBrowserWindow {
    static instances: FakeBrowserWindow[] = [];
    readonly loadFile = vi.fn().mockResolvedValue(undefined);

    constructor(readonly options: unknown) {
      FakeBrowserWindow.instances.push(this);
    }
  }

  return {
    handlers,
    BrowserWindow: FakeBrowserWindow,
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    }),
    showOpenDialog: vi.fn(),
    showSaveDialog: vi.fn(),
    writeFile: vi.fn(),
    whenReady: vi.fn(() => new Promise<never>(() => undefined)),
    on: vi.fn(),
    quit: vi.fn(),
  };
});

const processFakes = vi.hoisted(() => ({
  spawn: vi.fn(),
  execFileSync: vi.fn(),
  waitForPort: vi.fn(),
}));

vi.mock("electron", () => ({
  app: {
    whenReady: electronFakes.whenReady,
    on: electronFakes.on,
    quit: electronFakes.quit,
  },
  BrowserWindow: electronFakes.BrowserWindow,
  dialog: {
    showOpenDialog: electronFakes.showOpenDialog,
    showSaveDialog: electronFakes.showSaveDialog,
    showErrorBox: vi.fn(),
  },
  ipcMain: {
    handle: electronFakes.handle,
  },
}));

vi.mock("node:child_process", () => ({
  spawn: processFakes.spawn,
  execFileSync: processFakes.execFileSync,
}));

vi.mock("./handshake.js", () => ({
  waitForPort: processFakes.waitForPort,
}));

vi.mock("node:fs/promises", () => ({
  writeFile: electronFakes.writeFile,
}));

import { loadMainWindow, registerNativeBridge, stopBackendDuringExit } from "./main";

function handler(channel: string): (...args: unknown[]) => unknown {
  const registered = electronFakes.handlers.get(channel);
  if (!registered) throw new Error(`missing handler for ${channel}`);
  return registered;
}

describe("native Electron bridge handlers", () => {
  beforeEach(() => {
    electronFakes.handlers.clear();
    electronFakes.BrowserWindow.instances.length = 0;
    electronFakes.handle.mockClear();
    electronFakes.showOpenDialog.mockReset();
    electronFakes.showSaveDialog.mockReset();
    electronFakes.writeFile.mockReset();
    electronFakes.writeFile.mockResolvedValue(undefined);
    processFakes.spawn.mockReset();
    processFakes.execFileSync.mockReset();
    processFakes.waitForPort.mockReset();
  });

  it("opens one multi-selection PDF/image picker and keeps the backend port handler", async () => {
    const window = {} as BrowserWindow;
    electronFakes.showOpenDialog.mockResolvedValue({
      canceled: false,
      filePaths: ["C:/notes/one.pdf", "C:/notes/two.png"],
    });

    registerNativeBridge(window);

    await expect(handler("source:pick")({})).resolves.toEqual([
      "C:/notes/one.pdf",
      "C:/notes/two.png",
    ]);
    expect(electronFakes.showOpenDialog).toHaveBeenCalledWith(
      window,
      expect.objectContaining({
        properties: ["openFile", "multiSelections"],
      }),
    );
    const options = electronFakes.showOpenDialog.mock.calls[0][1] as {
      filters?: { extensions?: string[] }[];
    };
    const extensions = options.filters?.flatMap((filter) => filter.extensions ?? []) ?? [];
    expect(extensions).toEqual(expect.arrayContaining(["pdf", "png", "jpg", "jpeg"]));

    expect(handler("backend:port")({})).toBeNull();
  });

  it("returns an empty source list when the picker is canceled", async () => {
    electronFakes.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] });

    registerNativeBridge({} as BrowserWindow);

    await expect(handler("source:pick")({})).resolves.toEqual([]);
  });

  it("writes the exact selected artifact bytes after a successful save dialog", async () => {
    electronFakes.showSaveDialog.mockResolvedValue({
      canceled: false,
      filePath: "C:/exports/guide.html",
    });
    const bytes = new Uint8Array([0, 255, 10, 13, 128]).buffer;

    registerNativeBridge({} as BrowserWindow);

    await expect(handler("artifact:save")({}, "guide.html", bytes)).resolves.toEqual({
      canceled: false,
      path: "C:/exports/guide.html",
    });
    expect(electronFakes.showSaveDialog).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ defaultPath: "guide.html" }),
    );
    expect(electronFakes.writeFile).toHaveBeenCalledTimes(1);
    const [filePath, writtenBytes] = electronFakes.writeFile.mock.calls[0] as [string, Uint8Array];
    expect(filePath).toBe("C:/exports/guide.html");
    expect(Buffer.from(writtenBytes)).toEqual(Buffer.from(bytes));
  });

  it("does not write when the save dialog is canceled", async () => {
    electronFakes.showSaveDialog.mockResolvedValue({ canceled: true, filePath: "" });

    registerNativeBridge({} as BrowserWindow);

    await expect(handler("artifact:save")({}, "guide.html", new ArrayBuffer(0))).resolves.toEqual({
      canceled: true,
      path: null,
    });
    expect(electronFakes.writeFile).not.toHaveBeenCalled();
  });

  it("loads the window before backend startup so startup errors can be retried in the renderer", async () => {
    const window = await loadMainWindow();

    expect(window).toBe(electronFakes.BrowserWindow.instances[0]);
    expect(window.loadFile).toHaveBeenCalledTimes(1);
    expect(processFakes.spawn).not.toHaveBeenCalled();
  });

  it("uses synchronous taskkill tree cleanup during Windows process exit", () => {
    const child = Object.assign(new EventEmitter(), {
      killed: false,
      pid: 9876,
      exitCode: null,
      signalCode: null,
      kill: vi.fn(),
    });

    stopBackendDuringExit(child as ChildProcess, "win32");

    expect(processFakes.execFileSync).toHaveBeenCalledWith(
      "taskkill",
      ["/pid", "9876", "/T", "/F"],
      { stdio: "ignore", windowsHide: true },
    );
    expect(child.kill).not.toHaveBeenCalled();
  });

  it.each([
    ["returns false", () => false],
    ["throws", () => {
      throw new Error("kill failed");
    }],
  ])("falls back after taskkill failure and waits for a child that $0", async (_label, kill) => {
    const child = Object.assign(new EventEmitter(), {
      killed: false,
      pid: 4321,
      exitCode: null,
      signalCode: null,
      kill: vi.fn(kill),
    });
    const cleanup = new EventEmitter();
    processFakes.spawn.mockImplementation((command: string) => {
      return command === "taskkill" ? cleanup : child;
    });
    processFakes.waitForPort.mockRejectedValueOnce(new Error("backend failed"));

    registerNativeBridge({} as BrowserWindow);
    let settled = false;
    const attempt = handler("backend:start")({}) as Promise<number>;
    void attempt.then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      },
    );
    await vi.waitFor(() => {
      expect(processFakes.spawn.mock.calls.filter(([command]) => command === "taskkill")).toHaveLength(1);
    });

    cleanup.emit("close", 1);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(child.kill).toHaveBeenCalledTimes(1);
    expect(settled).toBe(false);

    child.emit("exit", 1, "SIGTERM");
    await expect(attempt).rejects.toThrow("backend failed");
  });

  it("waits for failed backend cleanup before retrying the spawn", async () => {
    const child = Object.assign(new EventEmitter(), {
      killed: false,
      pid: 4321,
      exitCode: null,
      signalCode: null,
      kill: vi.fn(),
    });
    const cleanup = new EventEmitter();
    processFakes.spawn.mockImplementation((command: string) => {
      return command === "taskkill" ? cleanup : child;
    });
    processFakes.waitForPort
      .mockRejectedValueOnce(new Error("backend failed"))
      .mockResolvedValueOnce(43121);
    const window = {} as BrowserWindow;

    registerNativeBridge(window);
    registerNativeBridge(window);

    let firstSettled = false;
    const firstAttempt = handler("backend:start")({}) as Promise<number>;
    void firstAttempt.then(
      () => {
        firstSettled = true;
      },
      () => {
        firstSettled = true;
      },
    );
    await vi.waitFor(() => {
      expect(
        processFakes.spawn.mock.calls.filter(([command]) => command === "taskkill"),
      ).toHaveLength(1);
    });
    expect(firstSettled).toBe(false);

    cleanup.emit("close", 0);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(firstSettled).toBe(false);
    expect(processFakes.spawn.mock.calls.filter(([command]) => command === "uv")).toHaveLength(1);
    child.emit("close", 0, null);
    await expect(firstAttempt).rejects.toThrow("backend failed");
    await expect(handler("backend:start")({})).resolves.toBe(43121);

    expect(processFakes.spawn.mock.calls.filter(([command]) => command === "uv")).toHaveLength(2);
    expect(electronFakes.handle.mock.calls.filter(([channel]) => channel === "backend:start")).toHaveLength(1);
    child.emit("close", 0, null);
  });

  it("rejects a retry when backend cleanup times out instead of spawning again", async () => {
    vi.useFakeTimers();
    try {
      const child = Object.assign(new EventEmitter(), {
        killed: false,
        pid: 4321,
        exitCode: null,
        signalCode: null,
        kill: vi.fn(() => false),
      });
      const cleanup = new EventEmitter();
      processFakes.spawn.mockImplementation((command: string) => {
        return command === "taskkill" ? cleanup : child;
      });
      processFakes.waitForPort.mockRejectedValueOnce(new Error("backend failed"));

      registerNativeBridge({} as BrowserWindow);
      const attempt = handler("backend:start")({}) as Promise<number>;
      for (
        let index = 0;
        index < 10 &&
        processFakes.spawn.mock.calls.filter(([command]) => command === "taskkill").length === 0;
        index += 1
      ) {
        await Promise.resolve();
      }
      expect(processFakes.spawn.mock.calls.filter(([command]) => command === "taskkill")).toHaveLength(1);

      const attemptFailure = expect(attempt).rejects.toThrow(
        "The previous backend process did not stop. Wait for it to exit before trying again.",
      );
      await vi.advanceTimersByTimeAsync(5_000);
      await attemptFailure;
      const uvSpawns = processFakes.spawn.mock.calls.filter(([command]) => command === "uv");
      await expect(handler("backend:start")({})).rejects.toThrow("previous backend process did not stop");
      expect(processFakes.spawn.mock.calls.filter(([command]) => command === "uv")).toHaveLength(
        uvSpawns.length,
      );
      child.emit("close", 1, null);
    } finally {
      vi.useRealTimers();
    }
  });

});
