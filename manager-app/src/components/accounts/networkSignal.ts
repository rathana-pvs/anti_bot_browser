import type { Profile } from '../../types/profile';
import type { ProxyItem } from '../../types/proxy';

// Kept here, not imported, so this file loads on its own in tests.
const GRADE_LABEL = { good: 'Working', slow: 'Working · slow', timeout: 'Timed out', dead: 'Not working' } as const;
const proxyWarnings = (proxy: ProxyItem) =>
  [proxy.rating?.stable === false && 'Unstable', proxy.check?.ip_changed && 'IP changed'].filter(Boolean) as string[];
import type { HealthSignal } from './accountModel';

/** Use the saved test for this account's selected pool proxy, never another proxy. */
export function networkSignal(profile: Profile, proxies: ProxyItem[] = []): HealthSignal {
  if (profile.runtime?.kind === 'android_user') {
    const reserved = profile.network.mode === 'pool' ? proxies.find(p => p.id === profile.network.proxy_id) : undefined;
    const check = profile.android_network;
    const when = check?.checked_at ? `checked ${new Date(check.checked_at).toLocaleString()}` : '';
    // Only a check run inside the phone's Android user can say the proxy is in use.
    if (reserved && check?.status === 'verified') return { label: 'Proxy verified', tone: 'ok', detail: `Exit ${check.exit_ip} · ${when}`,
      hint: `Measured inside the phone: traffic leaves through ${reserved.host}:${reserved.port} (exit ${check.exit_ip}), and the kill switch is on.` };
    if (reserved && check?.status === 'blocked') return { label: 'Proxy blocked', tone: 'bad', detail: when, hint: check.reason || 'The last check failed.' };
    if (reserved) return { label: 'Proxy reserved', tone: 'muted', detail: check?.checked_at ? 'Not set up on phone' : 'Not checked on phone yet',
      hint: check?.reason || `${reserved.host}:${reserved.port} is reserved for this account. Open the account and press Verify now to check it on the phone.` };
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
  if (proxy?.rating && proxy.check) {
    const { rating, check } = proxy;
    const one = (value: number | null) => value === null ? '—' : Number(value.toFixed(1)).toString();
    const detail = rating.grade === 'dead' ? (check.error || 'No traffic got through')
      : `↓${one(rating.download_mbps)} ↑${one(rating.upload_mbps)} Mbps`;
    const hint = [`${proxy.host}:${proxy.port} · ${GRADE_LABEL[rating.grade]}`,
      check.facebook_ms != null ? `Facebook responds in ${check.facebook_ms} ms` : '',
      ...proxyWarnings(proxy), `Checked ${new Date(check.checked_at).toLocaleString()}`].filter(Boolean).join('\n');
    if (rating.grade === 'dead') return { label: 'Proxy offline', tone: 'bad', detail, hint };
    if (rating.grade === 'timeout') return { label: 'Proxy timed out', tone: 'bad', detail, hint };
    if (rating.grade === 'slow') return { label: 'Proxy slow', tone: 'warn', detail, hint };
    return { label: proxyWarnings(proxy).length ? 'Proxy · watch' : 'Proxy good', tone: proxyWarnings(proxy).length ? 'warn' : 'ok', detail, hint };
  }
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
