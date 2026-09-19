import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { app, BrowserWindow, dialog, ipcMain } from "electron";

import { waitForPort } from "./handshake.js";

const desktopDir = path.dirname(fileURLToPath(import.meta.url));
const BACKEND_STOP_TIMEOUT_MS = 5_000;
const BACKEND_STOP_FAILURE_MESSAGE =
  "The previous backend process did not stop. Wait for it to exit before trying again.";
let backend: ChildProcess | null = null;
let backendPort: number | null = null;
let backendStartPromise: Promise<number> | null = null;
let backendStopPromise: Promise<boolean> | null = null;
let backendStopFailed = false;
let registeredBridgeWindow: BrowserWindow | null = null;
let shutdownPromise: Promise<boolean> | null = null;

function waitForProcessClose(child: ChildProcess): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cleanup = (): void => {
      child.removeListener("close", finish);
      child.removeListener("exit", finish);
      if (timer !== undefined) clearTimeout(timer);
    };
    const finish = (): void => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(true);
    };
    child.once("close", finish);
    child.once("exit", finish);
    timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(false);
    }, BACKEND_STOP_TIMEOUT_MS);
    if (
      (child.exitCode !== null && child.exitCode !== undefined) ||
      (child.signalCode !== null && child.signalCode !== undefined)
    ) {
      finish();
    }
  });
}

function hasExited(child: ChildProcess): boolean {
  return (
    (child.exitCode !== null && child.exitCode !== undefined) ||
    (child.signalCode !== null && child.signalCode !== undefined)
  );
}

function stopChildDirectly(
  child: ChildProcess,
  stopped: Promise<boolean> | undefined = undefined,
): Promise<boolean> {
  if (hasExited(child)) return Promise.resolve(true);
  const closeResult = stopped ?? waitForProcessClose(child);
  try {
    const killResult = child.killed ? true : child.kill();
    if (killResult === false) return closeResult;
  } catch {
    return closeResult;
  }
  return closeResult;
}

function runTaskkill(child: ChildProcess): Promise<boolean> {
  let terminator: ChildProcess;
  try {
    terminator = spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
  } catch {
    return Promise.resolve(false);
  }

  return new Promise((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cleanup = (): void => {
      terminator.removeListener("close", onClose);
      terminator.removeListener("error", onError);
      if (timer !== undefined) clearTimeout(timer);
    };
    const finish = (succeeded: boolean): void => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(succeeded);
    };
    const onClose = (code: number | null): void => {
      finish(code === 0);
    };
    const onError = (): void => {
      finish(false);
    };
    terminator.once("close", onClose);
    terminator.once("error", onError);
    timer = setTimeout(() => finish(false), BACKEND_STOP_TIMEOUT_MS);
    if (terminator.exitCode !== null && terminator.exitCode !== undefined) {
      onClose(terminator.exitCode);
    } else if (terminator.signalCode !== null && terminator.signalCode !== undefined) {
      onClose(null);
    }
  });
}

function stopChild(child: ChildProcess): Promise<boolean> {
  if (hasExited(child)) return Promise.resolve(true);

  if (process.platform === "win32" && child.pid !== undefined && child.pid !== null) {
    const childStopped = waitForProcessClose(child);
    return runTaskkill(child).then((taskkillSucceeded) => {
      if (!taskkillSucceeded) return stopChildDirectly(child, childStopped);
      return childStopped;
    });
  }

  return stopChildDirectly(child);
}

export function stopBackendDuringExit(
  child: ChildProcess,
  platform: NodeJS.Platform = process.platform,
): void {
  if (hasExited(child)) return;
  if (platform === "win32" && child.pid !== undefined && child.pid !== null) {
    try {
      execFileSync(
        "taskkill",
        ["/pid", String(child.pid), "/T", "/F"],
        { stdio: "ignore", windowsHide: true },
      );
      return;
    } catch {
      // Fall back to the direct child when the synchronous tree command fails.
    }
  }
  try {
    child.kill();
  } catch {
    // The process is already exiting; there is no further cleanup to await.
  }
}

function spawnBackend(): Promise<number> {
  let child: ChildProcess;
  try {
    child = spawn("uv", ["run", "python", "-m", "backend.api"], {
      cwd: path.resolve(desktopDir, "..", ".."),
      stdio: ["ignore", "pipe", "inherit"],
      windowsHide: true,
    });
  } catch (error: unknown) {
    return Promise.reject(error);
  }
  backend = child;
  child.once("close", () => {
    if (backend === child) {
      backend = null;
      backendPort = null;
      backendStopFailed = false;
    }
  });
  child.once("exit", () => {
    if (backend === child) {
      backend = null;
      backendPort = null;
      backendStopFailed = false;
    }
  });

  return waitForPort(child, 30_000).then(
    (port) => {
      if (backend !== child) throw new Error("backend stopped before startup completed");
      backendPort = port;
      return port;
    },
    async (error: unknown) => {
      const stopped = await stopBackend();
      if (!stopped) throw new Error(BACKEND_STOP_FAILURE_MESSAGE);
      throw error;
    },
  );
}

function startBackend(): Promise<number> {
  if (backendPort !== null) return Promise.resolve(backendPort);
  if (backendStartPromise !== null) return backendStartPromise;
  if (backendStopFailed || backend !== null) {
    return Promise.reject(new Error(BACKEND_STOP_FAILURE_MESSAGE));
  }

  backendStartPromise = (backendStopPromise ?? Promise.resolve(true))
    .then((stopped) => {
      if (!stopped || backendStopFailed || backend !== null) {
        throw new Error(BACKEND_STOP_FAILURE_MESSAGE);
      }
      return spawnBackend();
    })
    .finally(() => {
      backendStartPromise = null;
    });
  return backendStartPromise;
}

function stopBackend(): Promise<boolean> {
  if (backendStopPromise !== null) return backendStopPromise;
  const child = backend;
  backendPort = null;
  if (child === null) return Promise.resolve(true);
  backendStopPromise = stopChild(child)
    .then((stopped) => {
      if (stopped) {
        if (backend === child) backend = null;
        backendStopFailed = false;
      } else {
        backendStopFailed = true;
      }
      return stopped;
    })
    .finally(() => {
      backendStopPromise = null;
    });
  return backendStopPromise;
}

function requestShutdown(): void {
  if (shutdownPromise !== null) return;
  shutdownPromise = stopBackend().then((stopped) => {
    if (!stopped) {
      shutdownPromise = null;
      dialog.showErrorBox("Lesson Generator", BACKEND_STOP_FAILURE_MESSAGE);
      return false;
    }
    app.quit();
    return true;
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const sourceFilters = [
  {
    name: "PDF and images",
    extensions: ["pdf", "png", "jpg", "jpeg", "webp", "gif"],
  },
];

export interface ArtifactSaveResult {
  canceled: boolean;
  path: string | null;
}

export function registerNativeBridge(window: BrowserWindow): void {
  if (registeredBridgeWindow === window) return;
  registeredBridgeWindow = window;
  ipcMain.handle("backend:port", () => backendPort);
  ipcMain.handle("backend:start", () => startBackend());
  ipcMain.handle("source:pick", async () => {
    const result = await dialog.showOpenDialog(window, {
      properties: ["openFile", "multiSelections"],
      filters: sourceFilters,
    });
    return result.canceled ? [] : result.filePaths;
  });
  ipcMain.handle(
    "artifact:save",
    async (_event, defaultName: string, bytes: ArrayBuffer): Promise<ArtifactSaveResult> => {
      const result = await dialog.showSaveDialog(window, {
        defaultPath: defaultName,
        filters: [{ name: "HTML", extensions: ["html"] }],
      });
      if (result.canceled || result.filePath.length === 0) {
        return { canceled: true, path: null };
      }

      await writeFile(result.filePath, Buffer.from(bytes));
      return { canceled: false, path: result.filePath };
    },
  );
}

export async function loadMainWindow(): Promise<BrowserWindow> {
  const window = new BrowserWindow({
    width: 1280,
    height: 900,
    frame: false,
    titleBarOverlay: {
      color: "#ffffff",
      symbolColor: "#667085",
      height: 48,
    },
    webPreferences: {
      preload: path.join(desktopDir, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  registerNativeBridge(window);
  await window.loadFile(path.join(desktopDir, "..", "renderer", "index.html"));
  return window;
}

app.whenReady().then(async () => {
  try {
    await loadMainWindow();
  } catch (error: unknown) {
    dialog.showErrorBox("Lesson Generator", `Could not load the application: ${errorMessage(error)}`);
    app.quit();
  }
});

app.on("before-quit", (event) => {
  if (shutdownPromise !== null) return;
  event.preventDefault();
  requestShutdown();
});
app.on("window-all-closed", requestShutdown);
process.on("exit", () => {
  const child = backend;
  if (child !== null) stopBackendDuringExit(child);
});
