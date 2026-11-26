import { useCallback, useEffect, useState } from 'react';
import { isDesktopApp } from '../../services/desktopEnvironment';
import type { Profile } from '../../types/profile';

/** Window label prefix of an account's pop-out live window (`live-<profile id>`). */
export const LIVE_WINDOW_PREFIX = 'live-';

export const profileIdOfLabel = (label: string): string | null =>
  label.startsWith(LIVE_WINDOW_PREFIX) && label.length > LIVE_WINDOW_PREFIX.length ? label.slice(LIVE_WINDOW_PREFIX.length) : null;

/** What a window needs to open an account's live view. */
export interface LiveTarget { id: string; name: string; phone: boolean }

export const liveTargetOf = (profile: Profile): LiveTarget => ({
  id: profile.id, name: profile.name, phone: profile.runtime?.kind === 'android_user',
});

/** Opens (or brings forward) the account's live window. Any window of the app can call it. */
export async function openLiveWindow(target: LiveTarget): Promise<void> {
  const { invoke } = await import('@tauri-apps/api/core');
  await invoke('open_live_window', { profileId: target.id, title: target.name, phone: target.phone });
}

/**
 * The main window's view of the pop-outs: which accounts have their own live window now, so
 * the account page shows a placeholder instead of opening a second stream of the same screen.
 */
export function useLiveWindows() {
  const [popped, setPopped] = useState<ReadonlySet<string>>(() => new Set());
  const desktop = isDesktopApp();

  useEffect(() => {
    if (!desktop) return;
    let cancelled = false;
    const unlisteners: (() => void)[] = [];
    const change = (id: string, open: boolean) => setPopped(current => {
      if (current.has(id) === open) return current;
      const next = new Set(current);
      if (open) next.add(id); else next.delete(id);
      return next;
    });
    void (async () => {
      const [{ invoke }, { getCurrentWebviewWindow }] = await Promise.all([
        import('@tauri-apps/api/core'), import('@tauri-apps/api/webviewWindow'),
      ]);
      const current = getCurrentWebviewWindow();
      const opened = await current.listen<{ profileId: string }>('live-window-opened', event => change(event.payload.profileId, true));
      const closed = await current.listen<{ profileId: string }>('live-window-closed', event => change(event.payload.profileId, false));
      if (cancelled) { opened(); closed(); return; }
      unlisteners.push(opened, closed);
      const open = await invoke<string[]>('list_live_windows');
      if (!cancelled) setPopped(new Set(open));
    })().catch(() => undefined);
    return () => { cancelled = true; unlisteners.forEach(stop => stop()); };
  }, [desktop]);

  const open = useCallback((target: LiveTarget) => openLiveWindow(target), []);
  return { popped, open: desktop ? open : undefined };
}
