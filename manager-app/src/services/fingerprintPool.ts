import { ProfileFingerprint } from '../types/profile';

// Authentic Host Real Device Specifications (Intel TigerLake Iris Xe / i7-11370H / 16GB)
export const REAL_HOST_SPECS = {
  webgl_vendor: 'Intel Open Source Technology Center',
  webgl_renderer: 'Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2)',
  hardware_concurrency: 8,
  device_memory: 16,
  user_agent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  color_depth: 24,
  language: 'en-US',
};

export const LINUX_GPU_POOL: [string, string][] = [
  [REAL_HOST_SPECS.webgl_vendor, REAL_HOST_SPECS.webgl_renderer],
];

export interface ResolutionOption {
  value: string;
  label: string;
  category: string;
  recommended?: boolean;
}

export const RESOLUTION_OPTIONS: ResolutionOption[] = [
  { value: '1920x1080', label: '1920 × 1080', category: '16:9 Full HD', recommended: true },
  { value: '1600x900', label: '1600 × 900', category: '16:9 HD+' },
  { value: '1536x864', label: '1536 × 864', category: '16:9 Modern Laptop' },
  { value: '1440x900', label: '1440 × 900', category: '16:10 WXGA+' },
  { value: '1366x768', label: '1366 × 768', category: '16:9 Standard Laptop' },
  { value: '2560x1440', label: '2560 × 1440', category: '16:9 2K QHD' },
];

export const SCREEN_RESOLUTIONS: string[] = RESOLUTION_OPTIONS.map((o) => o.value);

export const TIMEZONES: string[] = [
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Berlin',
  'Asia/Tokyo',
];

export function generateAuthenticLinuxFingerprint(
  seedIndex: number = 0,
  resolution: string = '1920x1080',
  timezone?: string
): ProfileFingerprint {
  return {
    ...REAL_HOST_SPECS,
    screen_resolution: resolution,
    timezone: timezone || TIMEZONES[seedIndex % TIMEZONES.length],
  };
}
