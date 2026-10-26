import { useEffect, useState } from 'react';
import { reelApi } from '../services/reelCreator';
import type { Reel, SceneLayout } from '../types/reelCreator';

/** Element boxes of the scene preview, fetched with the same pause as the preview image; null while unknown. */
export function useSceneLayout(active: boolean, reel: Reel, scene: number): SceneLayout | null {
  const [layout, setLayout] = useState<SceneLayout | null>(null);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      reelApi.layout(reel, Math.min(scene, reel.scenes.length - 1), controller.signal)
        .then(setLayout).catch(() => { if (!controller.signal.aborted) setLayout(null); });
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, reel, scene]);
  return layout;
}
