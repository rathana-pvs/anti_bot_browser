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
  /** Browser account using this proxy. */
  profile_id: string | null;
  /** Phone account using this proxy; one proxy serves at most one of each. */
  phone_profile_id?: string | null;
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
