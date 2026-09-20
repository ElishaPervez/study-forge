import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

const electronFakes = vi.hoisted(() => ({
  exposeInMainWorld: vi.fn(),
  invoke: vi.fn(),
  on: vi.fn(),
  removeListener: vi.fn(),
  getPathForFile: vi.fn(),
}));

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const preloadPath = path.join(desktopDir, "dist", "preload.cjs");

function loadBuiltBridge(): {
  port: () => Promise<number | null>;
  startBackend: () => Promise<number>;
  pickSourceFiles: () => Promise<string[]>;
  pathForFile: (file: File) => string;
  saveArtifact: (defaultName: string, bytes: ArrayBuffer) => Promise<unknown>;
  minimizeWindow: () => Promise<void>;
  toggleMaximizeWindow: () => Promise<void>;
  isWindowMaximized: () => Promise<boolean>;
  closeWindow: () => Promise<void>;
  beginSubmission: () => Promise<number | null>;
  endSubmission: (token: number) => Promise<void>;
  onWindowMaximizedChange: (callback: (maximized: boolean) => void) => () => void;
} {
  const source = readFileSync(preloadPath, "utf8");
  const fakeRequire = (specifier: string): unknown => {
    if (specifier !== "electron") throw new Error(`unexpected module: ${specifier}`);
    return {
      contextBridge: {
        exposeInMainWorld: electronFakes.exposeInMainWorld,
      },
      ipcRenderer: {
        invoke: electronFakes.invoke,
        on: electronFakes.on,
        removeListener: electronFakes.removeListener,
      },
      webUtils: {
        getPathForFile: electronFakes.getPathForFile,
      },
    };
  };
  const moduleFactory = new Function("require", "exports", source) as (
    require: (specifier: string) => unknown,
    exports: object,
  ) => void;
  moduleFactory(fakeRequire, {});
  return electronFakes.exposeInMainWorld.mock.calls.at(-1)?.[1] as ReturnType<typeof loadBuiltBridge>;
}

describe("Electron preload build", () => {
  it("emits a CommonJS bridge that Electron can load", () => {
    expect(existsSync(preloadPath)).toBe(true);

    const source = readFileSync(preloadPath, "utf8");
    expect(source).toMatch(/require\(["']electron["']\)/);
    expect(source).not.toMatch(/^\s*import\s/m);
  });

  it("exposes the safe source and artifact bridge without Node primitives", async () => {
    const bridge = loadBuiltBridge();
    expect(bridge).toBeDefined();
    expect(typeof bridge.port).toBe("function");
    expect(typeof bridge.startBackend).toBe("function");
    expect(typeof bridge.pickSourceFiles).toBe("function");
    expect(typeof bridge.pathForFile).toBe("function");
    expect(typeof bridge.saveArtifact).toBe("function");
    expect(bridge).not.toHaveProperty("ipcRenderer");
    expect(bridge).not.toHaveProperty("require");
    expect(bridge).not.toHaveProperty("readFile");
    expect(bridge).not.toHaveProperty("writeFile");
    expect(bridge).not.toHaveProperty("pickPdf");

    electronFakes.invoke
      .mockResolvedValueOnce(43121)
      .mockResolvedValueOnce(43122)
      .mockResolvedValueOnce(["C:/notes/a.pdf"]);
    await expect(bridge.port()).resolves.toBe(43121);
    await expect(bridge.startBackend()).resolves.toBe(43122);
    await expect(bridge.pickSourceFiles()).resolves.toEqual(["C:/notes/a.pdf"]);
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(1, "backend:port");
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(2, "backend:start");
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(3, "source:pick");

    electronFakes.getPathForFile.mockReturnValue("C:/notes/a.pdf");
    const droppedFile = {} as File;
    expect(bridge.pathForFile(droppedFile)).toBe("C:/notes/a.pdf");
    expect(electronFakes.getPathForFile).toHaveBeenCalledWith(droppedFile);

    const bytes = new Uint8Array([0, 255, 12]).buffer;
    await bridge.saveArtifact("guide.html", bytes);
    expect(electronFakes.invoke).toHaveBeenLastCalledWith("artifact:save", "guide.html", bytes);
  });

  it("exposes the submission handshake so a quit can wait for it", async () => {
    electronFakes.invoke.mockReset();
    const bridge = loadBuiltBridge();
    expect(bridge).toBeDefined();
    expect(typeof bridge.beginSubmission).toBe("function");
    expect(typeof bridge.endSubmission).toBe("function");

    electronFakes.invoke.mockResolvedValueOnce(null).mockResolvedValueOnce(undefined);
    await expect(bridge.beginSubmission()).resolves.toBeNull();
    await bridge.endSubmission(7);
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(1, "submission:begin");
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(2, "submission:end", 7);

    electronFakes.invoke.mockResolvedValueOnce(3);
    await expect(bridge.beginSubmission()).resolves.toBe(3);
  });

  it("exposes whitelisted window controls through IPC", async () => {
    electronFakes.invoke.mockReset();
    electronFakes.on.mockReset();
    electronFakes.removeListener.mockReset();
    const bridge = loadBuiltBridge();
    expect(bridge).toBeDefined();
    expect(typeof bridge.minimizeWindow).toBe("function");
    expect(typeof bridge.toggleMaximizeWindow).toBe("function");
    expect(typeof bridge.isWindowMaximized).toBe("function");
    expect(typeof bridge.closeWindow).toBe("function");
    expect(typeof bridge.onWindowMaximizedChange).toBe("function");

    electronFakes.invoke.mockImplementation((channel: string) =>
      Promise.resolve(channel === "window:is-maximized" ? true : undefined),
    );
    await bridge.minimizeWindow();
    await bridge.toggleMaximizeWindow();
    await expect(bridge.isWindowMaximized()).resolves.toBe(true);
    await bridge.closeWindow();
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(1, "window:minimize");
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(2, "window:maximize-toggle");
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(3, "window:is-maximized");
    expect(electronFakes.invoke).toHaveBeenNthCalledWith(4, "window:close");

    const seen: boolean[] = [];
    const unsubscribe = bridge.onWindowMaximizedChange((maximized) => seen.push(maximized));
    expect(electronFakes.on).toHaveBeenCalledWith(
      "window:maximized-changed",
      expect.any(Function),
    );
    const listener = electronFakes.on.mock.calls.at(-1)?.[1] as (
      event: unknown,
      maximized: boolean,
    ) => void;
    listener({}, true);
    expect(seen).toEqual([true]);
    unsubscribe();
    expect(electronFakes.removeListener).toHaveBeenCalledWith(
      "window:maximized-changed",
      listener,
    );
  });
});
