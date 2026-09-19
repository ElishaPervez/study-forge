import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

const electronFakes = vi.hoisted(() => ({
  exposeInMainWorld: vi.fn(),
  invoke: vi.fn(),
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
});
