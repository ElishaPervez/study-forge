import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { app, BrowserWindow, dialog, ipcMain } from "electron";

import { waitForPort } from "./handshake.js";

const desktopDir = path.dirname(fileURLToPath(import.meta.url));
let backend: ChildProcess | null = null;
let backendPort: number | null = null;

function startBackend(): Promise<number> {
  const child = spawn("uv", ["run", "python", "-m", "backend.api"], {
    cwd: path.resolve(desktopDir, "..", ".."),
    stdio: ["ignore", "pipe", "inherit"],
    windowsHide: true,
  });
  backend = child;

  return waitForPort(child, 30_000).then(
    (port) => {
      backendPort = port;
      return port;
    },
    (error: unknown) => {
      stopBackend();
      throw error;
    },
  );
}

function stopBackend(): void {
  const child = backend;
  backend = null;
  backendPort = null;
  if (child === null || child.killed) return;
  if (process.platform === "win32" && child.pid !== undefined) {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

app.whenReady().then(async () => {
  try {
    await startBackend();
    const window = new BrowserWindow({
      width: 1280,
      height: 900,
      webPreferences: {
        preload: path.join(desktopDir, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });

    ipcMain.handle("backend:port", () => backendPort);
    ipcMain.handle("pdf:pick", async () => {
      const result = await dialog.showOpenDialog(window, {
        properties: ["openFile"],
        filters: [{ name: "PDF", extensions: ["pdf"] }],
      });
      return result.canceled ? null : result.filePaths[0];
    });

    await window.loadFile(path.join(desktopDir, "..", "renderer", "index.html"));
  } catch (error: unknown) {
    dialog.showErrorBox("Lesson Generator", `Could not start the backend: ${errorMessage(error)}`);
    app.quit();
  }
});

app.on("before-quit", stopBackend);
app.on("window-all-closed", () => {
  stopBackend();
  app.quit();
});
process.on("exit", stopBackend);
