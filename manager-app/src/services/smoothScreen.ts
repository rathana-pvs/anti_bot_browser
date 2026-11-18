/**
 * WebKit (the desktop app on Linux, Safari) scales a constantly redrawn canvas with blocky
 * nearest-pixel sampling, so the remote screen's text looks jagged; Chrome blends it.
 * In WebKit, noVNC's canvas keeps full size and stays on top, invisible, for the mouse and
 * keyboard, and each changed frame is copied onto a canvas below it with smooth scaling.
 */
export function needsSmoothScreen(userAgent = navigator.userAgent): boolean {
  return /AppleWebKit/.test(userAgent) && !/Chrome|Chromium|CriOS|Edg|OPR/.test(userAgent);
}

interface NoVncInternals {
  _canvas?: HTMLCanvasElement;
  _display?: { flip: (...args: unknown[]) => unknown };
}

/** Mirrors the RFB client's screen smoothly; stops by itself once its canvas leaves the page. */
export function mirrorScreenSmoothly(rfb: object, container: HTMLElement): void {
  // noVNC 1.7 internals; if they ever change, the screen simply stays as noVNC draws it.
  const { _canvas: source, _display: display } = rfb as NoVncInternals;
  const context = document.createElement('canvas').getContext('2d');
  if (!source || !display || typeof display.flip !== 'function' || !context) return;

  const mirror = context.canvas;
  mirror.setAttribute('aria-hidden', 'true');
  Object.assign(mirror.style, { position: 'absolute', pointerEvents: 'none', left: '0', top: '0' });
  container.prepend(mirror);
  source.style.position = 'relative';
  source.style.zIndex = '1';
  source.style.opacity = '0';

  let dirty = true;
  const flip = display.flip;
  display.flip = function (...args: unknown[]) {
    const result = flip.apply(this, args);
    dirty = true;
    return result;
  };

  let size = '';
  const draw = () => {
    if (!source.isConnected || !mirror.isConnected) {
      mirror.remove();
      return;
    }
    const box = source.getBoundingClientRect();
    const frame = container.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(box.width * ratio));
    const height = Math.max(1, Math.round(box.height * ratio));
    const nextSize = `${box.left - frame.left},${box.top - frame.top},${width},${height}`;
    if (nextSize !== size) {
      size = nextSize;
      Object.assign(mirror.style, {
        left: `${box.left - frame.left}px`, top: `${box.top - frame.top}px`,
        width: `${box.width}px`, height: `${box.height}px`,
      });
      mirror.width = width;
      mirror.height = height;
      dirty = true;
    }
    if (dirty && source.width > 0 && source.height > 0) {
      dirty = false;
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = 'high';
      context.drawImage(source, 0, 0, width, height);
    }
    requestAnimationFrame(draw);
  };
  requestAnimationFrame(draw);
}
