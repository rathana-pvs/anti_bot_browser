/** Pure helpers for AI Reel Studio documents. */
import type { Composition } from '../types/composition';

/** The highlight as whole words in the headline, ignoring case and spacing (as the renderer matches it). */
function highlighted(headline: string, phrase: string) {
  const text = headline.split(/\s+/).join(' '), words = phrase.trim().split(/\s+/).join(' ');
  if (!words) return false;
  return new RegExp(`(?<!\\w)${words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?!\\w)`, 'i').test(text);
}

/** A reel made before text could keep moving gets what the composer now gives every scene: the highlight swept in,
 * or an underline. A scene the operator set to "Stays still" has emphasis null, not missing, and is left alone. */
export function withTextMotion(composition: Composition): Composition {
  if (composition.scenes.some(s => 'emphasis' in s)) return composition;
  return { ...composition, scenes: composition.scenes.map(s => ({
    ...s, emphasis: s.kind === 'end' ? null : highlighted(s.headline, s.highlight) ? 'highlight_sweep' : 'underline',
  })) };
}
