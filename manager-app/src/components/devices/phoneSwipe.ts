/**
 * Turns mouse-wheel scrolling into a finger swipe, so the phone only ever sees touchscreen
 * input. Each swipe starts a small random distance from the cursor, so it scrolls what is
 * under it, and follows a slightly curved, eased path. It stays off the screen's edges,
 * where swipes open the notification shade, go Home or go Back.
 */
export interface SwipePoint {
  /** 0 down, 2 move, 1 up: the touch actions the phone viewer already sends. */
  action: 0 | 1 | 2;
  x: number;
  y: number;
  /** Milliseconds after the swipe starts. */
  at: number;
}

const MIN_DISTANCE = 0.15;
const MAX_DISTANCE = 0.45;
const STEP_MS = 16;
// The part of the screen a swipe may touch, as fractions of its width and height.
const SAFE_X = [0.08, 0.92] as const;
const SAFE_Y = [0.1, 0.9] as const;
// How far from the cursor a swipe may start.
const JITTER_X = 0.04;
const JITTER_Y = 0.03;

/** Wheel distance in pixels, whatever unit the browser reported it in. */
export function wheelPixels(deltaY: number, deltaMode: number, pageHeight: number): number {
  if (deltaMode === 1) return deltaY * 16;
  if (deltaMode === 2) return deltaY * pageHeight;
  return deltaY;
}

/**
 * One swipe for `wheel` pixels of scrolling (positive scrolls the content down, so the
 * finger moves up), in the phone's own pixel coordinates, starting near `cursor`
 * (the middle of the screen when there is none).
 */
export function planSwipe(wheel: number, width: number, height: number, random: () => number = Math.random,
  cursor?: { x: number; y: number }): SwipePoint[] {
  if (!wheel || width <= 0 || height <= 0) return [];
  const between = (low: number, high: number) => low + (high - low) * random();
  const within = (value: number, [low, high]: readonly [number, number], size: number) =>
    Math.min(high * size, Math.max(low * size, value));
  const up = wheel > 0;
  const origin = cursor ?? { x: width / 2, y: height / 2 };
  const startX = within(origin.x + between(-JITTER_X, JITTER_X) * width, SAFE_X, width);
  const startY = within(origin.y + between(-JITTER_Y, JITTER_Y) * height, SAFE_Y, height);
  const distance = Math.min(MAX_DISTANCE, Math.max(MIN_DISTANCE, Math.abs(wheel) * 1.2 / height)) * height;
  // Near the top or bottom the swipe is shortened rather than reaching the edge.
  const endY = within(startY + (up ? -distance : distance), SAFE_Y, height);
  const endX = within(startX + between(-0.03, 0.03) * width, SAFE_X, width);
  // A finger's path bows a little to one side.
  const bow = between(-0.02, 0.02) * width;
  const duration = between(140, 260);
  const steps = Math.max(4, Math.round(duration / STEP_MS));
  // Every point of the path, not only its ends, stays inside the safe area.
  const clampX = (x: number) => Math.round(within(x, SAFE_X, width));
  const clampY = (y: number) => Math.round(within(y, SAFE_Y, height));

  const points: SwipePoint[] = [{ action: 0, x: clampX(startX), y: clampY(startY), at: 0 }];
  for (let step = 1; step <= steps; step += 1) {
    const t = step / steps;
    const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
    const x = startX + (endX - startX) * eased + bow * Math.sin(Math.PI * t);
    const y = startY + (endY - startY) * eased;
    points.push({ action: step === steps ? 1 : 2, x: clampX(x), y: clampY(y), at: Math.round(t * duration) });
  }
  return points;
}
