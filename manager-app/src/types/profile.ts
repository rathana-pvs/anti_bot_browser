export interface ProfileFingerprint {
  webgl_vendor: string;
  webgl_renderer: string;
  user_agent: string;
  screen_resolution: string;
  color_depth: number;
  timezone: string;
  language: string;
  hardware_concurrency: number;
  device_memory: number;
}

export interface ProfileNetwork {
  proxy_type: string;
  proxy_host: string;
  proxy_port: number;
  proxy_user: string;
  proxy_pass: string;
}

export interface ProfileContainer {
  id: string | null;
  vnc_port: number;
  ws_port?: number;
  volume_path: string;
}

export interface ProfileAccount {
  platform: string;
  email: string;
  notes: string;
  warming_start_date: string | null;
  warming_complete: boolean;
  warming_week: number;
  posts_today: number;
  last_post_date: string | null;
}

export interface Profile {
  id: string;
  created_at: string;
  name: string;
  status: 'running' | 'paused' | 'stopped' | 'error';
  fingerprint: ProfileFingerprint;
  network: ProfileNetwork;
  container: ProfileContainer;
  account: ProfileAccount;
  ram_usage?: string;
  cpu_usage?: string;
}

export interface SystemStats {
  docker_running: boolean;
  active_profiles: number;
  total_profiles: number;
  used_memory_mb: number;
  total_memory_mb: number;
  available_proxies: number;
  cpu_percent?: number;
  cpu_cores?: number;
  cpu_model?: string;
  gpu_percent?: number;
  gpu_model?: string;
}
