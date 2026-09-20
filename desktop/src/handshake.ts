export const ANNOUNCE_PREFIX = "STUDY_FORGE_PORT=";

type EventListener = (...args: any[]) => void;

interface OutputStream {
  on(event: string | symbol, listener: EventListener): unknown;
  off(event: string | symbol, listener: EventListener): unknown;
}

export interface PortProcess {
  stdout: OutputStream | null;
  once(event: string | symbol, listener: EventListener): unknown;
  off(event: string | symbol, listener: EventListener): unknown;
}

export function parseAnnouncedPort(line: string): number | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith(ANNOUNCE_PREFIX)) return null;
  const raw = trimmed.slice(ANNOUNCE_PREFIX.length);
  if (!/^[0-9]+$/.test(raw)) return null;
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  return port;
}

function chunkToText(chunk: unknown): string {
  if (typeof chunk === "string") return chunk;
  return (chunk as { toString(encoding?: string): string }).toString("utf8");
}

export function waitForPort(process: PortProcess, timeoutMs: number): Promise<number> {
  const stdout = process.stdout;
  if (stdout === null) {
    return Promise.reject(new Error("backend stdout is unavailable"));
  }

  return new Promise((resolve, reject) => {
    let buffered = "";
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const cleanup = (): void => {
      stdout.off("data", onData);
      process.off("error", onError);
      process.off("exit", onExit);
      if (timer !== undefined) {
        clearTimeout(timer);
        timer = undefined;
      }
    };

    const settle = (finish: () => void): void => {
      if (settled) return;
      settled = true;
      cleanup();
      finish();
    };

    const onData = (chunk: unknown): void => {
      buffered += chunkToText(chunk);
      const lines = buffered.split(/\r?\n/);
      buffered = lines.pop() ?? "";
      for (const line of lines) {
        const port = parseAnnouncedPort(line);
        if (port !== null) {
          settle(() => resolve(port));
          return;
        }
      }
    };

    const onError = (error: unknown): void => {
      settle(() => reject(error));
    };

    const onExit = (code: unknown): void => {
      settle(() => reject(new Error(`backend exited before announcing a port (${code})`)));
    };

    stdout.on("data", onData);
    process.once("error", onError);
    process.once("exit", onExit);
    timer = setTimeout(() => {
      settle(() => reject(new Error(`backend did not announce a port in ${timeoutMs}ms`)));
    }, timeoutMs);
  });
}
