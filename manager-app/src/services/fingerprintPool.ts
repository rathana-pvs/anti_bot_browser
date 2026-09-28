/** Browser-environment choices that the container launcher can actually apply. */
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

export const SCREEN_RESOLUTIONS = RESOLUTION_OPTIONS.map((option) => option.value);

/** Used only as a temporary UI fallback until backend defaults arrive. */
export function getHostTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}
