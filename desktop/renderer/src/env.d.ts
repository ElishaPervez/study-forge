export {};

declare global {
  interface Window {
    lessonGen?: {
      port: () => Promise<number | null>;
      pickPdf: () => Promise<string | null>;
    };
  }
}
