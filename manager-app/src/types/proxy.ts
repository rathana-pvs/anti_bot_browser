export const PROXY_FOLDERS = ['Used', 'Available', 'Poor proxy'] as const;

export interface ProxyItem {
  id: string;
  group?: string;
  speed_quality?: 'good' | 'poor' | 'untested' | 'check_failed';
  last_speed_quality?: 'good' | 'poor';
  speed_test?: import('../services/api').NetworkSpeedResult;
  /** The last full check: quick check plus timed speed samples. */
  check?: ProxyCheck;
  /** The check rated against the current rules. */
  rating?: ProxyRating;
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

/** good: both ways ≥ 1 Mbps · slow: working, a direction under 1 Mbps · timeout: a direction got nothing through · dead: unreachable. */
export type ProxyGrade = 'good' | 'slow' | 'timeout' | 'dead';

export interface ProxySample { mbps: number | null; bytes: number; error?: string | null }

export interface ProxyCheck {
  checked_at: string;
  reachable: boolean;
  connect_ms: number | null;
  facebook_ok: boolean;
  facebook_ms: number | null;
  facebook_status?: number | null;
  exit_ip: string | null;
  ip_changed?: boolean;
  error?: string | null;
  download: ProxySample[];
  upload: ProxySample[];
  seconds?: number;
  history?: { at: string; grade: ProxyGrade; down: number | null; up: number | null; facebook_ms: number | null; exit_ip: string | null }[];
}

export interface ProxyRating {
  grade: ProxyGrade;
  stable: boolean | null;
  download_mbps: number | null;
  upload_mbps: number | null;
}

export const GRADE_LABEL: Record<ProxyGrade, string> = {
  good: 'Working', slow: 'Working · slow', timeout: 'Timed out', dead: 'Not working',
};
export const GRADE_TONE: Record<ProxyGrade, string> = {
  good: 'text-emerald-400', slow: 'text-amber-300', timeout: 'text-red-400', dead: 'text-red-400',
};

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
const GRADE_ORDER: ProxyGrade[] = ['good', 'slow', 'timeout', 'dead'];

/** Working first, then slow, untested, timed out, not working. */
const qualityRank = (proxy: ProxyItem) => proxy.rating
  ? [0, 1, 3, 4][GRADE_ORDER.indexOf(proxy.rating.grade)]
  : [0.5, 2, 3.5, 4][QUALITY_ORDER.indexOf(proxy.speed_quality ?? 'untested')];

export const proxyQualityLabel = (proxy: ProxyItem) =>
  proxy.rating ? GRADE_LABEL[proxy.rating.grade] : QUALITY_LABEL[proxy.speed_quality ?? 'untested'];

/** Warnings worth showing beside the rating. */
export function proxyWarnings(proxy: ProxyItem): string[] {
  return [proxy.rating?.stable === false && 'Unstable', proxy.check?.ip_changed && 'IP changed'].filter(Boolean) as string[];
}
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
  return proxies.filter(proxy => hasRoom(proxy, profileId))
    .sort((a, b) => qualityRank(a) - qualityRank(b) || proxyHolders(a).length - proxyHolders(b).length);
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

const located2 = (p: ProxyItem) => p.geo_source !== 'unknown';

/** Great-circle distance between two proxies' exit locations, when both are known. */
export function distanceKm(a: ProxyItem, b: ProxyItem): number | null {
  if (!located2(a) || !located2(b) || a.latitude == null || a.longitude == null || b.latitude == null || b.longitude == null) return null;
  const rad = (d: number) => d * Math.PI / 180;
  const dLat = rad(b.latitude - a.latitude), dLon = rad(b.longitude - a.longitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.latitude)) * Math.cos(rad(b.latitude)) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/** How close `proxy` is to `from`: 0 same city, 1 same state, 2 same timezone, 3 elsewhere, 4 unknown. */
function nearTier(from: ProxyItem, proxy: ProxyItem) {
  if (!located2(from) || !located2(proxy)) return 4;
  const same = (key: 'city' | 'region' | 'timezone') => Boolean(from[key]) && from[key] === proxy[key] && from.country_code === proxy.country_code;
  return same('city') ? 0 : same('region') ? 1 : same('timezone') ? 2 : 3;
}

/** "same city", "same state", "same time zone" or "≈ 330 km", for picker labels. */
export function nearnessLabel(from: ProxyItem | undefined, proxy: ProxyItem): string | null {
  if (!from || from.id === proxy.id) return null;
  const tier = nearTier(from, proxy);
  if (tier === 0) return 'same city';
  if (tier === 1) return 'same state';
  const km = distanceKm(from, proxy);
  if (tier === 2) return km == null ? 'same time zone' : `same time zone · ≈ ${Math.round(km)} km`;
  return km == null ? null : `≈ ${Math.round(km).toLocaleString()} km away`;
}

/** Proxies nearest to `from` first, so a replacement keeps the account where Facebook knows it. */
export function nearestFirst(proxies: ProxyItem[], from: ProxyItem | undefined): ProxyItem[] {
  if (!from || !located2(from)) return proxies;
  const key = (p: ProxyItem) => [nearTier(from, p), distanceKm(from, p) ?? Number.MAX_VALUE] as const;
  return [...proxies].sort((a, b) => { const [ta, da] = key(a), [tb, db] = key(b); return ta - tb || da - db; });
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
