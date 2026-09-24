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
  country?: string;
  country_code?: string;
  region?: string;
  city?: string;
  timezone?: string;
  last_checked: string | null;
}
