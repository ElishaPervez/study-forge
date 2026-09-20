export {};

declare global {
  interface Window {
    studyForge?: {
      port: () => Promise<number | null>;
      startBackend: () => Promise<number>;
      pickSourceFiles: () => Promise<string[]>;
      pathForFile: (file: File) => string;
      saveArtifact: (defaultName: string, bytes: ArrayBuffer) => Promise<{
        canceled: boolean;
        path: string | null;
      }>;
      minimizeWindow: () => Promise<void>;
      toggleMaximizeWindow: () => Promise<void>;
      isWindowMaximized: () => Promise<boolean>;
      closeWindow: () => Promise<void>;
      onWindowMaximizedChange: (callback: (maximized: boolean) => void) => () => void;
    };
  }
}
