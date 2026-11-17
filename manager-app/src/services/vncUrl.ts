/** LAN screens use the authenticated gateway; native and local viewers keep loopback ports. */
export function vncWebSocketUrl(profileId: string, port: number,
  location: { hostname: string; host: string; protocol: string }): string {
  const { hostname, host, protocol } = location;
  if (!hostname || hostname === 'tauri.localhost' || protocol === 'tauri:') {
    return `ws://127.0.0.1:${port}`;
  }
  if (hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '[::1]') {
    return `ws://${hostname}:${port}`;
  }
  return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${host}/_lan/vnc/${encodeURIComponent(profileId)}`;
}
