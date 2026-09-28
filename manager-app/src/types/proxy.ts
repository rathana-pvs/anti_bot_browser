export interface ProxyItem {
  id: string;
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
