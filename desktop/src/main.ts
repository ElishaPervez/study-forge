import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { app, BrowserWindow, dialog, ipcMain } from "electron";

import {
  createCloseGuard,
  type CloseGuard,
  type CloseStatus,
  type CloseWarning,
} from "./closeGuard.js";
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
let closeApproved = false;
let closeGuard: CloseGuard | null = null;
let submissionsFrozen = false;
let nextSubmissionToken = 1;
const openSubmissions = new Set<number>();

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

function activeWindow(): BrowserWindow | null {
  const window = registeredBridgeWindow;
  if (window === null || window.isDestroyed()) return null;
  return window;
}

async function serviceCloseStatus(action: "prepare" | "resume" | "confirm"): Promise<CloseStatus> {
  const port = backendPort;
  if (port === null) throw new Error("the study service is not running");
  const response = await fetch(`http://127.0.0.1:${port}/api/shutdown/${action}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`the study service answered ${response.status}`);
  }
  return (await response.json()) as CloseStatus;
}

async function askToQuit(warning: CloseWarning): Promise<boolean> {
  const options = {
    type: "warning" as const,
    buttons: ["Keep app open", "Quit and stop work"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
    title: "Study Forge",
    message: warning.message,
    detail: warning.detail,
  };
  const window = activeWindow();
  const result =
    window === null
      ? await dialog.showMessageBox(options)
      : await dialog.showMessageBox(window, options);
  // Keeping the app open is the safe default: only the second button quits.
  return result.response === 1;
}

function closeGuardInstance(): CloseGuard {
  if (closeGuard !== null) return closeGuard;
  closeGuard = createCloseGuard({
    prepare: () => serviceCloseStatus("prepare"),
    resume: () => serviceCloseStatus("resume"),
    confirm: () => serviceCloseStatus("confirm"),
    warn: askToQuit,
    setSubmissionsFrozen: (frozen) => {
      submissionsFrozen = frozen;
    },
    pendingSubmissions: () => openSubmissions.size,
    serviceMaybeRunning: () => backendPort !== null || backend !== null,
    stopBackend: () => stopBackend(),
    reportStopFailure: () => dialog.showErrorBox("Study Forge", BACKEND_STOP_FAILURE_MESSAGE),
    quit: () => {
      closeApproved = true;
      app.quit();
    },
  });
  return closeGuard;
}

/** One decision for the close button, Alt+F4, and app quit. */
async function attemptClose(): Promise<boolean> {
  return await closeGuardInstance().requestClose();
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
  ipcMain.handle("window:minimize", () => {
    window.minimize();
  });
  ipcMain.handle("window:maximize-toggle", () => {
    if (window.isMaximized()) {
      window.unmaximize();
    } else {
      window.maximize();
    }
  });
  ipcMain.handle("window:is-maximized", () => window.isMaximized());
  ipcMain.handle("window:close", () => {
    window.close();
  });
  ipcMain.handle("submission:begin", () => {
    if (submissionsFrozen) return null;
    const token = nextSubmissionToken;
    nextSubmissionToken += 1;
    openSubmissions.add(token);
    return token;
  });
  ipcMain.handle("submission:end", (_event, token: unknown) => {
    if (typeof token === "number") openSubmissions.delete(token);
  });
  const notifyMaximizedChanged = (): void => {
    if (window.isDestroyed()) return;
    window.webContents.send("window:maximized-changed", window.isMaximized());
  };
  window.on("maximize", notifyMaximizedChanged);
  window.on("unmaximize", notifyMaximizedChanged);
  window.on("restore", notifyMaximizedChanged);
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
    webPreferences: {
      preload: path.join(desktopDir, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  registerNativeBridge(window);
  window.on("close", (event) => {
    // The final, already-approved pass closes for real.
    if (closeApproved) return;
    event.preventDefault();
    void attemptClose();
  });
  await window.loadFile(path.join(desktopDir, "..", "renderer", "index.html"));
  window.show?.();
  window.focus?.();
  return window;
}

app.whenReady().then(async () => {
  try {
    await loadMainWindow();
  } catch (error: unknown) {
    dialog.showErrorBox("Study Forge", `Could not load the application: ${errorMessage(error)}`);
    app.quit();
  }
});

app.on("before-quit", (event) => {
  if (closeApproved) return;
  event.preventDefault();
  void attemptClose();
});
app.on("window-all-closed", () => {
  app.quit();
});
process.on("exit", () => {
  const child = backend;
  if (child !== null) stopBackendDuringExit(child);
});
