import { contextBridge, ipcRenderer, webUtils } from "electron";

const pickSourceFiles = (): Promise<string[]> => ipcRenderer.invoke("source:pick");

contextBridge.exposeInMainWorld("lessonGen", {
  port: (): Promise<number | null> => ipcRenderer.invoke("backend:port"),
  startBackend: (): Promise<number> => ipcRenderer.invoke("backend:start"),
  pickSourceFiles,
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  saveArtifact: (defaultName: string, bytes: ArrayBuffer) =>
    ipcRenderer.invoke("artifact:save", defaultName, bytes),
});
