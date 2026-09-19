export {};

declare global {
  interface Window {
    lessonGen?: {
      port: () => Promise<number | null>;
      startBackend: () => Promise<number>;
      pickSourceFiles: () => Promise<string[]>;
      pathForFile: (file: File) => string;
      saveArtifact: (defaultName: string, bytes: ArrayBuffer) => Promise<{
        canceled: boolean;
        path: string | null;
      }>;
    };
  }
}
