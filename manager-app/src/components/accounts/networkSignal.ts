import type { Profile } from '../../types/profile';
import type { ProxyItem } from '../../types/proxy';
import type { HealthSignal } from './accountModel';

/** Use the saved test for this account's selected pool proxy, never another proxy. */
export function networkSignal(profile: Profile, proxies: ProxyItem[] = []): HealthSignal {
  if (profile.runtime?.kind === 'android_user') {
    const reserved = profile.network.mode === 'pool' ? proxies.find(p => p.id === profile.network.proxy_id) : undefined;
    if (reserved) return { label: 'Proxy reserved', tone: 'muted', detail: 'Not on phone yet',
      hint: `${reserved.host}:${reserved.port} is reserved for this account. The phone uses its own network until the companion app tunnels through the proxy.` };
    return { label: 'Not set up', tone: 'muted', detail: "Phone's own network",
      hint: "Proxy and kill switch for phone accounts are not set up yet. This account uses the phone's own network." };
  }
  const network = profile.network;
  const preflight = profile.effective_environment?.network_preflight;
  if (!network.proxy_host) {
    if (preflight?.startsWith('passed:')) return { label: 'Direct OK', tone: 'ok', hint: preflight };
    return preflight ? { label: 'Network failed', tone: 'bad', hint: preflight }
      : { label: 'Direct', tone: 'muted', hint: 'No proxy' };
  }
  const proxy = network.mode === 'pool'
    ? proxies.find(p => p.id === network.proxy_id)
    : undefined;
  if (proxy) {
    const test = proxy.speed_test;
    const samples = [test?.download, test?.upload];
    const measured = samples.map(s => s?.success === true && typeof s.mbps === 'number' && Number.isFinite(s.mbps) && s.mbps >= 0 ? s.mbps : null);
    const format = (value: number | null) => value === null ? '—' : Number(value.toFixed(2)).toString();
    const detail = test ? `↓${format(measured[0])} ↑${format(measured[1])} Mbps`
      : `Speed untested${proxy.latency_ms != null ? ` · ${proxy.latency_ms} ms` : ''}`;
    const hint = [
      `${proxy.host}:${proxy.port}`,
      test ? `Download: ${measured[0] === null ? test.download?.error || 'not confirmed' : measured[0] + ' Mbps'}; upload: ${measured[1] === null ? test.upload?.error || 'not confirmed' : measured[1] + ' Mbps'}` : 'Download/upload have not been tested.',
      test?.last_checked ? `Speed checked: ${new Date(test.last_checked).toLocaleString()}` : '',
      proxy.latency_ms != null ? `Latency: ${proxy.latency_ms} ms` : '',
      proxy.last_checked ? `Connection checked: ${new Date(proxy.last_checked).toLocaleString()}` : '',
    ].filter(Boolean).join('\n');
    const connectionTime = Date.parse(proxy.last_checked || '') || 0;
    const speedTime = Date.parse(test?.last_checked || '') || 0;
    if (proxy.reachable === false && connectionTime >= speedTime) return { label: 'Proxy offline', tone: 'bad', detail, hint };
    if (measured.some(value => value !== null && value < 1)) return { label: 'Proxy slow', tone: 'warn', detail, hint };
    if (test && measured.every(value => value !== null && value >= 1)) return { label: 'Proxy good', tone: 'ok', detail, hint };
    if (test) return { label: 'Proxy test failed', tone: 'warn', detail, hint };
    return { label: 'Speed untested', tone: 'muted', detail, hint };
  }
  // Custom proxies have no pool test record. Preserve their browser-start check.
  if (preflight?.startsWith('passed:')) return { label: 'Proxy online', tone: 'ok', detail: 'Speed untested', hint: preflight };
  if (preflight) return { label: 'Proxy failed', tone: 'bad', hint: preflight };
  return { label: 'Proxy · not checked', tone: 'muted', hint: `${network.proxy_host}:${network.proxy_port} · checked when the browser starts` };
}
