import { useCallback, useEffect, useRef, useState } from 'react';
import { isDesktopApp } from '../../services/desktopEnvironment';
import type { MiniNavigateTarget } from '../../MiniWindowApp';

/** `docked`: inside the main window. `window`: popped out into its own window. `hidden`: off. */
export type StatusPanelPlace = 'docked' | 'window' | 'hidden';

const STORAGE_KEY = 'manager.statusPanel';

const readSaved = (): StatusPanelPlace => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    // A popped-out window doesn't survive a restart; it comes back docked.
    return saved === 'docked' || saved === 'window' ? 'docked' : 'hidden';
  } catch {
    return 'hidden';
  }
};

/**
 * Where the status panel is, plus the main window's side of the pop-out: it opens the
 * floating window, docks the panel again when that window closes, and follows its links.
 */
export function useStatusPanel(onNavigate: (target: MiniNavigateTarget) => void) {
  const [place, setPlace] = useState<StatusPanelPlace>(readSaved);
  const navigateRef = useRef(onNavigate);
  navigateRef.current = onNavigate;

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, place); } catch { /* the choice just isn't remembered */ }
  }, [place]);

  useEffect(() => {
    if (!isDesktopApp()) return;
    let cancelled = false;
    const unlisteners: (() => void)[] = [];
    void import('@tauri-apps/api/webviewWindow').then(async ({ getCurrentWebviewWindow }) => {
      const current = getCurrentWebviewWindow();
      const docked = await current.listen('mini-docked', () => setPlace('docked'));
      const navigate = await current.listen<MiniNavigateTarget>('mini-navigate', event => navigateRef.current(event.payload));
      if (cancelled) { docked(); navigate(); } else unlisteners.push(docked, navigate);
    }).catch(() => undefined);
    return () => { cancelled = true; unlisteners.forEach(stop => stop()); };
  }, []);

  const popOut = useCallback(async () => {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('open_mini_window');
    setPlace('window');
  }, []);

  const toggle = useCallback(() => {
    if (place === 'window') {
      void import('@tauri-apps/api/core').then(({ invoke }) => invoke('open_mini_window')).catch(() => undefined);
      return;
    }
    setPlace(place === 'docked' ? 'hidden' : 'docked');
  }, [place]);

  return { place, setPlace, popOut: isDesktopApp() ? popOut : undefined, toggle };
}
