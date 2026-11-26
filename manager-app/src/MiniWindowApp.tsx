import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { MiniStatus } from './components/mini/MiniStatus';
import { openLiveWindow } from './components/live/liveWindows';

/** Where the mini window asks the main window to go; App.tsx handles the `mini-navigate` event. */
export type MiniNavigateTarget =
  | { page: 'queue'; executionId?: string; profileId?: string }
  | { page: 'live'; profileId: string };

const showMain = (target?: MiniNavigateTarget) => invoke('show_main_window', { target: target ?? null }).catch(() => undefined);

/** The whole page of the floating status window (Tauri window label `mini`). */
export function MiniWindowApp() {
  const [onTop, setOnTop] = useState(true);

  useEffect(() => {
    // The window has no frame, so the page itself carries the background.
    document.documentElement.classList.add('mini-window');
    return () => document.documentElement.classList.remove('mini-window');
  }, []);

  const toggleOnTop = () => {
    const next = !onTop;
    invoke('set_mini_on_top', { onTop: next }).then(() => setOnTop(next)).catch(() => undefined);
  };

  return <div onContextMenu={event => event.preventDefault()} className="font-sans">
    <MiniStatus mode="window"
      onTop={onTop}
      onToggleOnTop={toggleOnTop}
      onOpenApp={() => void showMain()}
      onOpenQueue={(executionId, profileId) => void showMain({ page: 'queue', executionId, profileId })}
      onOpenLive={target => void openLiveWindow(target).catch(() => showMain({ page: 'live', profileId: target.id }))}
      onClose={() => void invoke('dock_mini_window').catch(() => undefined)} />
  </div>;
}
