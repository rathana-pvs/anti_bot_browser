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
  /** Known only by address: the proxy is kept on a phone another PC set up. */
  held_by_phone?: boolean;
  type: string;
  assigned: boolean;
  /** First account using the proxy; `profile_ids` lists all of them. */
  profile_id: string | null;
  profile_ids?: string[];
  /** How many accounts one proxy may serve, from the Proxies page setting. */
  account_limit?: number;
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

/** One IP per line for a provider's replace page. Holds no port or password; proxies sharing an IP appear once. */
export function proxyIpListText(proxies: ProxyItem[]): string {
  return [...new Set(proxies.map(proxy => proxy.host))].join('\n') + '\n';
}

const QUALITY_LABEL: Record<NonNullable<ProxyItem['speed_quality']>, string> = {
  good: 'Good', poor: 'Poor', check_failed: 'Test failed', untested: 'Untested',
};
const QUALITY_ORDER = ['good', 'untested', 'poor', 'check_failed'];

export const proxyQualityLabel = (proxy: ProxyItem) => QUALITY_LABEL[proxy.speed_quality ?? 'untested'];
export const isPoorProxy = (proxy: ProxyItem) => proxy.group === 'Poor proxy';

/** The accounts using this proxy. */
export function proxyHolders(proxy: ProxyItem): string[] {
  if (Array.isArray(proxy.profile_ids)) return proxy.profile_ids;
  return proxy.profile_id ? [proxy.profile_id] : [];
}

export const accountLimit = (proxy: ProxyItem) => Math.max(1, proxy.account_limit ?? 1);

/** True when the account already uses the proxy or the proxy is below its account limit. */
export function hasRoom(proxy: ProxyItem, profileId?: string) {
  const holders = proxyHolders(proxy);
  return (profileId !== undefined && holders.includes(profileId)) || holders.length < accountLimit(proxy);
}

/** "2/3 accounts", for pickers and badges. */
export const proxyUsageLabel = (proxy: ProxyItem) => {
  const limit = accountLimit(proxy);
  return `${proxyHolders(proxy).length}/${limit} ${limit === 1 ? 'account' : 'accounts'}`;
};

/** Proxies with room for another account, best first, then least shared. Poor ones stay listed so the user can still choose them knowingly. */
export function freeProxies(proxies: ProxyItem[], profileId?: string) {
  const rank = (proxy: ProxyItem) => QUALITY_ORDER.indexOf(proxy.speed_quality ?? 'untested');
  return proxies.filter(proxy => hasRoom(proxy, profileId))
    .sort((a, b) => rank(a) - rank(b) || proxyHolders(a).length - proxyHolders(b).length);
}

/** Matches any state or city in the location filters. */
export const ANY_LOCATION = '__any__';

const located = (proxies: ProxyItem[]) => proxies.filter(p => p.geo_source !== 'unknown');
const distinct = (values: (string | null | undefined)[]) =>
  [...new Set(values.filter((v): v is string => Boolean(v)))].sort((a, b) => a.localeCompare(b));

/** States with their proxy counts, and the cities in the chosen state (all cities for any state). */
export function proxyLocations(proxies: ProxyItem[], state = ANY_LOCATION) {
  const known = located(proxies);
  const inState = known.filter(p => state === ANY_LOCATION || p.region === state);
  return {
    states: distinct(known.map(p => p.region)).map(name => ({ name, count: known.filter(p => p.region === name).length })),
    cities: distinct(inState.map(p => p.city)).map(name => ({ name, count: inState.filter(p => p.city === name).length })),
  };
}

/** Proxies in the chosen state and city. Proxies with an unknown location match only "any". `keepId` always stays listed,
 *  so a picker never loses its current choice. */
export function filterByLocation(proxies: ProxyItem[], state: string, city: string, keepId?: string | null) {
  return proxies.filter(p => p.id === keepId || (
    (state === ANY_LOCATION || (p.geo_source !== 'unknown' && p.region === state)) &&
    (city === ANY_LOCATION || (p.geo_source !== 'unknown' && p.city === city))));
}

/** The pool proxy an account uses, if any. */
export const proxyOfProfile = (profile: { network: { mode?: string; proxy_id?: string | null } }, proxies: ProxyItem[]) =>
  profile.network.mode === 'pool' && profile.network.proxy_id ? proxies.find(p => p.id === profile.network.proxy_id) : undefined;

export const proxyOptionLabel = (proxy: ProxyItem) =>
  `${proxyQualityLabel(proxy)} · ${proxy.host}:${proxy.port} — ${formatProxyGeography(proxy)}` +
  (accountLimit(proxy) > 1 ? ` · ${proxyUsageLabel(proxy)}` : '');

/** The speed test saved on the Proxies page, so choosing a proxy never tests it again. */
export function savedSpeedSummary(proxy: ProxyItem): string {
  const test = proxy.speed_test;
  if (!test) return 'Not tested yet. Test it on the Proxies page.';
  const mbps = (sample?: { success?: boolean; mbps?: number | null }) =>
    sample?.success && typeof sample.mbps === 'number' ? Number(sample.mbps.toFixed(1)).toString() : '—';
  const when = test.last_checked ? ` · tested ${new Date(test.last_checked).toLocaleString()}` : '';
  return `↓${mbps(test.download)} ↑${mbps(test.upload)} Mbps${when}`;
}
