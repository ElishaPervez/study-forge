import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("lessonGen", {
  port: (): Promise<number | null> => ipcRenderer.invoke("backend:port"),
  pickPdf: (): Promise<string | null> => ipcRenderer.invoke("pdf:pick"),
});
