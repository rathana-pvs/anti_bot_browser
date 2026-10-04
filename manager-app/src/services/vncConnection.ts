interface DisplayClient {
  disconnect(): void;
  addEventListener(type: string, listener: () => void): void;
}

interface ConnectionOptions<T extends DisplayClient> {
  createClient: () => T;
  onClient: (client: T | null) => void;
  onStatus: (status: 'connecting' | 'connected' | 'disconnected', retries: number) => void;
}

// Each attempt owns its callbacks and deadline. Retiring it before disconnect()
// prevents late events from an old socket from disrupting the current display.
export function startVncConnection<T extends DisplayClient>({
  createClient, onClient, onStatus,
}: ConnectionOptions<T>) {
  let stopped = false;
  let retries = 0;
  let current: { client: T | null } | null = null;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  const maxRetries = 20;

  const retire = () => {
    clearTimeout(deadline);
    deadline = undefined;
    const previous = current;
    current = null;
    onClient(null);
    try { previous?.client?.disconnect(); } catch { /* Already closed. */ }
  };

  const connect = () => {
    if (stopped) return;
    retire();
    onStatus('connecting', retries);
    const attempt: { client: T | null } = { client: null };
    current = attempt;
    const isCurrent = () => !stopped && current === attempt;

    const failed = () => {
      if (!isCurrent()) return;
      retire();
      if (retries >= maxRetries) {
        onStatus('disconnected', retries);
        return;
      }
      retries += 1;
      onStatus('connecting', retries);
      retryTimer = setTimeout(connect, Math.min(3000, 600 + retries * 150));
    };

    try {
      const client = createClient();
      attempt.client = client;
      onClient(client);
      // A socket can remain open without completing the RFB handshake.
      deadline = setTimeout(failed, 10000);
      client.addEventListener('connect', () => {
        if (!isCurrent()) return;
        clearTimeout(deadline);
        deadline = undefined;
        retries = 0;
        onStatus('connected', 0);
      });
      client.addEventListener('disconnect', failed);
      client.addEventListener('securityfailure', failed);
    } catch {
      failed();
    }
  };

  connect();
  return () => {
    stopped = true;
    clearTimeout(retryTimer);
    retire();
  };
}
