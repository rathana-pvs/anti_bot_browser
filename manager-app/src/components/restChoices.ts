/** How long an account rests between its posts in a batch (batch_iteration_delay_seconds). */
export const REST_CHOICES = [0, 15, 30, 60, 120, 300, 600, 1800, 3600];
export const DEFAULT_REST_SECONDS = 30;
export const MAX_REST_SECONDS = 3600;

export function restLabel(seconds: number): string {
  if (seconds <= 0) return 'No rest';
  if (seconds >= 3600 && seconds % 3600 === 0) return seconds === 3600 ? '1 hour' : `${seconds / 3600} hours`;
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60), rest = seconds % 60;
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/** The choices for a dropdown, keeping a current value that is not one of them. */
export function restOptions(current: number): { value: string; label: string }[] {
  return [...new Set([...REST_CHOICES, current])].sort((a, b) => a - b)
    .map(seconds => ({ value: String(seconds), label: restLabel(seconds) }));
}

/** A saved rest, or the default. 60 was the old default and is read as "never chosen". */
export function savedRest(saved: string | null, chosen: boolean): number {
  const value = saved === null ? NaN : parseInt(saved, 10);
  if (Number.isNaN(value) || (!chosen && value === 60)) return DEFAULT_REST_SECONDS;
  return Math.min(MAX_REST_SECONDS, Math.max(0, value));
}
