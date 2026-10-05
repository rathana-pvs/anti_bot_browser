/** Identify desktop sessions by Tauri markers, independently of browser ports. */
export const isDesktopApp = (): boolean =>
  typeof window !== 'undefined' && (
    '__TAURI_INTERNALS__' in window ||
    '__TAURI__' in window ||
    window.location.protocol === 'tauri:' ||
    window.location.hostname === 'tauri.localhost'
  );
