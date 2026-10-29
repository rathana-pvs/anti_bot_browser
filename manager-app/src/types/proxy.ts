export const PROXY_FOLDERS = ['Used', 'Available', 'Poor proxy'] as const;

export interface ProxyItem {
  id: string;
  group?: string;
  speed_quality?: 'good' | 'poor' | 'untested' | 'check_failed';
  last_speed_quality?: 'good' | 'poor';
  speed_test?: import('../services/api').NetworkSpeedResult;
  host: string;
  port: number;
  username: string;
  password?: string;
  type: string;
  assigned: boolean;
  profile_id: string | null;
  latency_ms: number | null;
  reachable?: boolean | null;
  country?: string | null;
  country_code?: string | null;
  region?: string | null;
  city?: string | null;
  timezone?: string | null;
  geo_source?: 'unknown' | 'stored' | 'manual' | 'ip_lookup' | 'proxy_exit_lookup';
  exit_ip?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  geo_checked_at?: string | null;
  geo_last_error?: string | null;
  last_checked: string | null;
}

export function formatProxyGeography(proxy: ProxyItem): string {
  if (proxy.geo_source === 'unknown') return 'Location unknown';
  const country = proxy.country_code || proxy.country;
  const location = [proxy.city, country].filter(Boolean).join(', ');
  return [location, proxy.timezone].filter(Boolean).join(' · ') || 'Location unknown';
}

const QUALITY_LABEL: Record<NonNullable<ProxyItem['speed_quality']>, string> = {
  good: 'Good', poor: 'Poor', check_failed: 'Test failed', untested: 'Untested',
};
const QUALITY_ORDER = ['good', 'untested', 'poor', 'check_failed'];

export const proxyQualityLabel = (proxy: ProxyItem) => QUALITY_LABEL[proxy.speed_quality ?? 'untested'];
export const isPoorProxy = (proxy: ProxyItem) => proxy.group === 'Poor proxy';

/** Proxies no account holds, best first. Poor ones stay listed so the user can still choose them knowingly. */
export function freeProxies(proxies: ProxyItem[]) {
  const rank = (proxy: ProxyItem) => QUALITY_ORDER.indexOf(proxy.speed_quality ?? 'untested');
  return proxies.filter(proxy => !proxy.assigned && !proxy.profile_id).sort((a, b) => rank(a) - rank(b));
}

export const proxyOptionLabel = (proxy: ProxyItem) =>
  `${proxyQualityLabel(proxy)} · ${proxy.host}:${proxy.port} — ${formatProxyGeography(proxy)}`;

/** The speed test saved on the Proxies page, so choosing a proxy never tests it again. */
export function savedSpeedSummary(proxy: ProxyItem): string {
  const test = proxy.speed_test;
  if (!test) return 'Not tested yet. Test it on the Proxies page.';
  const mbps = (sample?: { success?: boolean; mbps?: number | null }) =>
    sample?.success && typeof sample.mbps === 'number' ? Number(sample.mbps.toFixed(1)).toString() : '—';
  const when = test.last_checked ? ` · tested ${new Date(test.last_checked).toLocaleString()}` : '';
  return `↓${mbps(test.download)} ↑${mbps(test.upload)} Mbps${when}`;
}
