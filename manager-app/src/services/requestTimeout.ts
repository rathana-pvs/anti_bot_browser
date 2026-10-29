/** Bound the entire request, including response-body reads, and cancel on unmount. */
export async function withRequestTimeout<T>(
  request: (signal: AbortSignal) => Promise<T>,
  signal?: AbortSignal,
  timeoutMs = 15000,
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel = () => {};
  const interrupted = new Promise<never>((_, reject) => {
    cancel = () => {
      controller.abort();
      reject(new Error('Request cancelled'));
    };
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error('Evidence request timed out. Try again.'));
    }, timeoutMs);
  });
  signal?.addEventListener('abort', cancel, { once: true });
  try {
    if (signal?.aborted) cancel();
    return await Promise.race([
      interrupted,
      controller.signal.aborted ? interrupted : request(controller.signal),
    ]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}
