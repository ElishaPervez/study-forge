import { contextBridge, ipcRenderer, webUtils, type IpcRendererEvent } from "electron";

const pickSourceFiles = (): Promise<string[]> => ipcRenderer.invoke("source:pick");

contextBridge.exposeInMainWorld("studyForge", {
  port: (): Promise<number | null> => ipcRenderer.invoke("backend:port"),
  startBackend: (): Promise<number> => ipcRenderer.invoke("backend:start"),
  pickSourceFiles,
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  saveArtifact: (defaultName: string, bytes: ArrayBuffer) =>
    ipcRenderer.invoke("artifact:save", defaultName, bytes),
  minimizeWindow: (): Promise<void> => ipcRenderer.invoke("window:minimize"),
  toggleMaximizeWindow: (): Promise<void> => ipcRenderer.invoke("window:maximize-toggle"),
  isWindowMaximized: (): Promise<boolean> => ipcRenderer.invoke("window:is-maximized"),
  closeWindow: (): Promise<void> => ipcRenderer.invoke("window:close"),
  onWindowMaximizedChange: (callback: (maximized: boolean) => void): (() => void) => {
    const listener = (_event: IpcRendererEvent, maximized: boolean): void => {
      callback(maximized);
    };
    ipcRenderer.on("window:maximized-changed", listener);
    return () => {
      ipcRenderer.removeListener("window:maximized-changed", listener);
    };
  },
});
