import { EventEmitter } from "node:events";
import type { BrowserWindow } from "electron";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const electronFakes = vi.hoisted(() => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>();
  class FakeBrowserWindow {
    static instances: FakeBrowserWindow[] = [];
    readonly loadFile = vi.fn().mockResolvedValue(undefined);
    readonly minimize = vi.fn();
    readonly maximize = vi.fn();
    readonly unmaximize = vi.fn();
    readonly close = vi.fn();
    readonly isMaximized = vi.fn(() => maximizedState.value);
    readonly isDestroyed = vi.fn(() => false);
    readonly on = vi.fn((event: string, listener: () => void) => {
      windowEvents.set(event, listener);
    });
    readonly webContents = { send: vi.fn() };

    constructor(readonly options: unknown) {
      FakeBrowserWindow.instances.push(this);
    }
  }

  const maximizedState = { value: false };
  const windowEvents = new Map<string, () => void>();
  const appEvents = new Map<string, () => void>();

  return {
    handlers,
    maximizedState,
    windowEvents,
    appEvents,
    BrowserWindow: FakeBrowserWindow,
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    }),
    showOpenDialog: vi.fn(),
    showSaveDialog: vi.fn(),
    showMessageBox: vi.fn(),
    writeFile: vi.fn(),
    whenReady: vi.fn(() => new Promise<never>(() => undefined)),
    on: vi.fn((event: string, listener: () => void) => {
      appEvents.set(event, listener);
    }),
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
    showMessageBox: electronFakes.showMessageBox,
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

function fakeWindow(): BrowserWindow {
  return new electronFakes.BrowserWindow({}) as unknown as BrowserWindow;
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
    const window = fakeWindow();
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

  it("wires window controls to the registered BrowserWindow", async () => {
    const window = new electronFakes.BrowserWindow({}) as unknown as BrowserWindow & {
      minimize: ReturnType<typeof vi.fn>;
      maximize: ReturnType<typeof vi.fn>;
      unmaximize: ReturnType<typeof vi.fn>;
      close: ReturnType<typeof vi.fn>;
      webContents: { send: ReturnType<typeof vi.fn> };
    };
    electronFakes.maximizedState.value = false;

    registerNativeBridge(window);

    handler("window:minimize")({});
    expect(window.minimize).toHaveBeenCalledTimes(1);

    handler("window:maximize-toggle")({});
    expect(window.maximize).toHaveBeenCalledTimes(1);
    electronFakes.maximizedState.value = true;
    handler("window:maximize-toggle")({});
    expect(window.unmaximize).toHaveBeenCalledTimes(1);
    expect(handler("window:is-maximized")({})).toBe(true);

    handler("window:close")({});
    expect(window.close).toHaveBeenCalledTimes(1);

    for (const event of ["maximize", "unmaximize", "restore"]) {
      const notify = electronFakes.windowEvents.get(event);
      expect(notify).toBeDefined();
      notify?.();
      expect(window.webContents.send).toHaveBeenLastCalledWith(
        "window:maximized-changed",
        true,
      );
    }
  });

  it("returns an empty source list when the picker is canceled", async () => {
    electronFakes.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] });

    registerNativeBridge(fakeWindow());

    await expect(handler("source:pick")({})).resolves.toEqual([]);
  });

  it("writes the exact selected artifact bytes after a successful save dialog", async () => {
    electronFakes.showSaveDialog.mockResolvedValue({
      canceled: false,
      filePath: "C:/exports/guide.html",
    });
    const bytes = new Uint8Array([0, 255, 10, 13, 128]).buffer;

    registerNativeBridge(fakeWindow());

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

    registerNativeBridge(fakeWindow());

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

    registerNativeBridge(fakeWindow());
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
    const window = fakeWindow();

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

      registerNativeBridge(fakeWindow());
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

type CloseEvent = { preventDefault: () => void };

describe("close protection", () => {
  beforeEach(() => {
    electronFakes.handlers.clear();
    electronFakes.BrowserWindow.instances.length = 0;
    electronFakes.windowEvents.clear();
    electronFakes.appEvents.clear();
    electronFakes.handle.mockClear();
    electronFakes.showMessageBox.mockReset();
    electronFakes.showMessageBox.mockResolvedValue({ response: 0 });
    electronFakes.quit.mockClear();
    processFakes.spawn.mockReset();
    processFakes.execFileSync.mockReset();
    processFakes.waitForPort.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function freshMain(): Promise<typeof import("./main")> {
    vi.resetModules();
    return await import("./main");
  }

  function closeListener(): ((event: CloseEvent) => void) | undefined {
    return electronFakes.windowEvents.get("close") as unknown as
      | ((event: CloseEvent) => void)
      | undefined;
  }

  it("pairs every submission handshake with a matching end", async () => {
    const mod = await freshMain();
    await mod.loadMainWindow();

    const first = handler("submission:begin")({});
    const second = handler("submission:begin")({});
    expect(typeof first).toBe("number");
    expect(typeof second).toBe("number");
    expect(second).not.toBe(first);

    handler("submission:end")({}, first);
    handler("submission:end")({}, second);
  });

  it("closes quietly and refuses new submissions when nothing is unresolved", async () => {
    const mod = await freshMain();
    await mod.loadMainWindow();

    const event: CloseEvent = { preventDefault: vi.fn() };
    closeListener()?.(event);

    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(electronFakes.quit).toHaveBeenCalledTimes(1));
    expect(electronFakes.showMessageBox).not.toHaveBeenCalled();
    // Once quitting is confirmed, no new submission may start.
    expect(handler("submission:begin")({})).toBeNull();
  });

  it("warns about an unresolved submission and restores it when the user stays", async () => {
    const mod = await freshMain();
    await mod.loadMainWindow();
    const token = handler("submission:begin")({});
    expect(typeof token).toBe("number");

    const event: CloseEvent = { preventDefault: vi.fn() };
    closeListener()?.(event);

    await vi.waitFor(() => expect(electronFakes.showMessageBox).toHaveBeenCalledTimes(1));
    const options = electronFakes.showMessageBox.mock.calls[0]?.[1] as {
      buttons?: string[];
      defaultId?: number;
    };
    expect(options.buttons).toEqual(["Keep app open", "Quit and stop work"]);
    // Keeping the app open is the safe default.
    expect(options.defaultId).toBe(0);
    expect(electronFakes.quit).not.toHaveBeenCalled();

    // Staying reopens submissions and stops the handshake that was unresolved.
    expect(handler("submission:begin")({})).not.toBeNull();
    handler("submission:end")({}, token);
  });

  it("prompts once when the close gesture is repeated", async () => {
    const mod = await freshMain();
    await mod.loadMainWindow();
    handler("submission:begin")({});

    let release: (value: { response: number }) => void = () => undefined;
    electronFakes.showMessageBox.mockImplementation(
      () => new Promise<{ response: number }>((resolve) => {
        release = resolve;
      }),
    );
    const listener = closeListener();
    const first: CloseEvent = { preventDefault: vi.fn() };
    const second: CloseEvent = { preventDefault: vi.fn() };
    listener?.(first);
    await vi.waitFor(() => expect(electronFakes.showMessageBox).toHaveBeenCalledTimes(1));
    listener?.(second);

    expect(second.preventDefault).toHaveBeenCalledTimes(1);
    expect(electronFakes.showMessageBox).toHaveBeenCalledTimes(1);

    release({ response: 0 });
    await vi.waitFor(() => expect(handler("submission:begin")({})).not.toBeNull());
    expect(electronFakes.quit).not.toHaveBeenCalled();
  });

  it("uses the same decision for app quit as for the window close button", async () => {
    const mod = await freshMain();
    await mod.loadMainWindow();
    handler("submission:begin")({});

    const beforeQuit = electronFakes.appEvents.get("before-quit") as unknown as
      | ((event: CloseEvent) => void)
      | undefined;
    expect(beforeQuit).toBeDefined();
    const event: CloseEvent = { preventDefault: vi.fn() };
    beforeQuit?.(event);

    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(electronFakes.showMessageBox).toHaveBeenCalledTimes(1));
  });

  it("stops work and quits when the user confirms quitting", async () => {
    const mod = await freshMain();
    await mod.loadMainWindow();
    handler("submission:begin")({});
    electronFakes.showMessageBox.mockResolvedValue({ response: 1 });

    const event: CloseEvent = { preventDefault: vi.fn() };
    closeListener()?.(event);

    await vi.waitFor(() => expect(electronFakes.quit).toHaveBeenCalledTimes(1));
    expect(handler("submission:begin")({})).toBeNull();
  });

  it("keeps the app open and reports failure when the service process will not stop", async () => {
    const child = Object.assign(new EventEmitter(), {
      killed: false,
      pid: 4321,
      exitCode: null,
      signalCode: null,
      kill: vi.fn(() => false),
    });
    const terminator = new EventEmitter();
    processFakes.spawn.mockImplementation((command: string) =>
      command === "taskkill" ? terminator : child,
    );
    processFakes.waitForPort.mockResolvedValue(4321);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          active: 1,
          waiting: 0,
          accepting: false,
          confirmed: false,
        }),
      })),
    );

    vi.useFakeTimers();
    try {
      const mod = await freshMain();
      await mod.loadMainWindow();
      await handler("backend:start")({});
      electronFakes.showMessageBox.mockResolvedValue({ response: 1 });

      const event: CloseEvent = { preventDefault: vi.fn() };
      closeListener()?.(event);
      await vi.waitFor(() => expect(electronFakes.showMessageBox).toHaveBeenCalledTimes(1));

      terminator.emit("close", 1);
      await vi.advanceTimersByTimeAsync(5_000);

      // The app must not claim the work stopped when the process survived.
      expect(electronFakes.quit).not.toHaveBeenCalled();
      // Staying open means submissions may start again.
      expect(handler("submission:begin")({})).not.toBeNull();
      child.emit("close", 1, null);
    } finally {
      vi.useRealTimers();
    }
  });
});
