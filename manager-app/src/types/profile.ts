export interface ProfileFingerprint {
  webgl_vendor: string | null;
  webgl_renderer: string | null;
  user_agent: string;
  screen_resolution: string;
  color_depth: number;
  timezone: string;
  language: string;
  hardware_concurrency: number | null;
  device_memory: number | null;
}

export type NetworkMode = 'direct' | 'pool' | 'custom';

export interface NetworkIntent {
  mode: NetworkMode;
  proxy_id?: string | null;
  host?: string | null;
  port?: number | null;
  username?: string;
  password?: string;
}

export interface RequestedEnvironment {
  screen_resolution: string;
  timezone_policy: 'host' | 'proxy' | 'manual';
  timezone: string | null;
  language: string;
  user_agent_policy: 'browser_default';
  user_agent: null;
  rendering_mode: 'host_gpu' | 'software';
}

export interface EffectiveEnvironment extends RequestedEnvironment {
  network_mode: NetworkMode;
  proxy_endpoint: string | null;
  configuration_revision: number;
  container_id: string;
  applied_at: string;
  network_preflight?: string | null;
}

export interface ObservedEnvironment {
  source: string;
  observed_at: string;
  screen_resolution?: string | null;
  timezone?: string | null;
  language?: string | null;
  languages?: string[] | null;
  system_locale?: string | null;
  browser_version?: string | null;
  webgl_vendor?: string | null;
  webgl_renderer?: string | null;
  rendering?: {
    requested_mode: 'host_gpu' | 'software';
    effective_mode: 'host_gpu' | 'software';
    device_backend: 'drm' | 'wsl_dxg' | 'none';
    accelerated: boolean;
    renderer: string;
    display_renderer?: string | null;
    browser_webgl_vendor?: string | null;
    browser_webgl_renderer?: string | null;
    fallback_reason: string;
  } | null;
  user_agent?: string | null;
  platform?: string | null;
  hardware_concurrency?: number | null;
  device_memory?: number | null;
  webdriver?: boolean | null;
  screen?: {
    width?: number;
    height?: number;
    avail_width?: number;
    avail_height?: number;
    color_depth?: number;
    pixel_depth?: number;
    device_pixel_ratio?: number;
    viewport_width?: number;
    viewport_height?: number;
  } | null;
  user_agent_data?: Record<string, unknown> | null;
  comparison?: Record<string, 'matched' | 'different' | 'not_measured'>;
  note?: string;
}

export interface ResourceLimits {
  cpu_limit: number;
  memory_mb: number;
}

export type BehaviorMode = 'fast' | 'medium' | 'slow';
export type ReelTemplateSelection = 'auto' | 't1' | 't2' | 't3';

export const reelTemplateLabel = (value: ReelTemplateSelection): string => ({
  auto: 'Auto detect',
  t1: 'T1 — Studio',
  t2: 'T2A — Direct',
  t3: 'T2B — Next / Share',
}[value]);

export interface AutomationPreferences {
  reel_template: ReelTemplateSelection;
}

export interface EffectiveResourceLimits extends ResourceLimits {
  configuration_revision: number;
  applied_at: string;
}

export interface ProfileNetwork {
  mode: NetworkMode;
  proxy_id?: string | null;
  proxy_type: string;
  proxy_host: string;
  proxy_port: number | null;
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
  group?: string;
  schema_version?: number;
  configuration_revision?: number;
  id: string;
  created_at: string;
  name: string;
  status: 'running' | 'paused' | 'stopped' | 'error';
  fingerprint: ProfileFingerprint;
  network: ProfileNetwork;
  container: ProfileContainer;
  account: ProfileAccount;
  requested_environment?: RequestedEnvironment;
  effective_environment?: EffectiveEnvironment | null;
  observed_environment?: ObservedEnvironment | null;
  restart_required?: boolean;
  resources?: ResourceLimits;
  effective_resources?: EffectiveResourceLimits | null;
  behavior_mode?: BehaviorMode;
  automation?: AutomationPreferences;
  ram_usage?: string;
  cpu_usage?: string;
  disk_usage?: string;
}

export interface ProfileCreateRequest {
  group?: string;
  name: string;
  network: NetworkIntent;
  requested_environment: RequestedEnvironment;
  resources: ResourceLimits;
  behavior_mode: BehaviorMode;
  automation: AutomationPreferences;
  account?: Partial<ProfileAccount>;
}

export interface ProfileUpdateRequest {
  group?: string;
  name?: string;
  network?: NetworkIntent;
  requested_environment?: RequestedEnvironment;
  resources?: ResourceLimits;
  behavior_mode?: BehaviorMode;
  automation?: AutomationPreferences;
  account?: Partial<ProfileAccount>;
}

export interface ProfileDefaults {
  schema_version: number;
  supported_resolutions: string[];
  default_environment: RequestedEnvironment;
  default_resources: ResourceLimits;
  default_behavior_mode: BehaviorMode;
  behavior_modes: BehaviorMode[];
  default_automation: AutomationPreferences;
  reel_template_options: ReelTemplateSelection[];
  resource_options: {
    cpu_limits: number[];
    memory_mb: number[];
    host_cpu_threads: number;
    host_memory_mb: number;
  };
  enforceable_fields: string[];
  observed_fields: string[];
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
  windows_host?: {
    available: boolean;
    cpu_percent?: number;
    cpu_threads?: number;
    cpu_model?: string;
    used_memory_mb?: number;
    total_memory_mb?: number;
    gpu_percent?: number | null;
    gpu_model?: string | null;
  };
  runtime_environment?: 'wsl' | 'linux';
  runtime?: {
    cpu_percent: number;
    cpu_threads: number;
    cpu_model: string;
    used_memory_mb: number;
    total_memory_mb: number;
    gpu_percent?: number;
    gpu_model?: string;
  };
  wsl_runtime?: SystemStats['runtime'];
}
