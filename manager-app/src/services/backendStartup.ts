/** Probe only a read endpoint; never replay a failed user action during startup. */
export interface StartupOptions {
  signal: AbortSignal;
  timeoutMs?: number;
  retryMs?: number;
  requestTimeoutMs?: number;
  now?: () => number;
  delay?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
}

function abortError(): Error {
  return new DOMException('Backend connection check cancelled', 'AbortError');
}

export function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(abortError()); return; }
    const finish = () => { signal.removeEventListener('abort', cancel); resolve(); };
    const timer = setTimeout(finish, milliseconds);
    const cancel = () => { clearTimeout(timer); signal.removeEventListener('abort', cancel); reject(abortError()); };
    signal.addEventListener('abort', cancel, { once: true });
  });
}

export async function waitForBackend(
  probe: (signal: AbortSignal) => Promise<number>,
  { signal, timeoutMs = 60_000, retryMs = 750, requestTimeoutMs = 2_000,
    now = () => performance.now(), delay = abortableDelay }: StartupOptions,
): Promise<void> {
  const deadline = now() + timeoutMs;
  while (!signal.aborted && now() < deadline) {
    const request = new AbortController();
    const cancel = () => request.abort();
    signal.addEventListener('abort', cancel, { once: true });
    let timer: ReturnType<typeof setTimeout> | undefined;
    let status: number | undefined;
    try {
      // Race bounds even a probe that ignores AbortSignal (e.g. a stalled bridge).
      const timeout = new Promise<number>((resolve) => {
        timer = setTimeout(() => { request.abort(); resolve(0); }, Math.min(requestTimeoutMs, deadline - now()));
      });
      const aborted = new Promise<number>((resolve) => {
        request.signal.addEventListener('abort', () => resolve(0), { once: true });
      });
      status = await Promise.race([probe(request.signal), timeout, aborted]);
    } catch {
      // Connection refusal and temporary HTTP failures are expected during boot.
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
      request.abort();
    }
    if (signal.aborted) throw abortError();
    if (status !== undefined && status >= 200 && status < 300) return;
    if (status === 401 || status === 403) {
      throw new Error('The app could not authenticate with its backend. Reopen the app to reconnect.');
    }
    const remaining = deadline - now();
    if (remaining > 0) await delay(Math.min(retryMs, remaining), signal);
  }
  if (signal.aborted) throw abortError();
  throw new Error('The backend has not responded yet. Retry the connection, or use Setup and repair if it keeps happening.');
}
