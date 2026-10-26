import { Input } from '../ui/Input';
import type { LookInfo, Reel, ReelLook, ReelRatio } from '../../types/reelCreator';

/** Format and look: two ratios, four ready-made layouts; the label is the only thing worth typing over. */
export function LookPicker({ looks, ratios, reel, onLook, onTag, onRatio }: {
  looks: LookInfo[]; ratios: ReelRatio[]; reel: Reel;
  onLook: (look: ReelLook) => void; onTag: (tag: string | null) => void; onRatio: (ratio: ReelRatio) => void;
}) {
  const current = looks.find(look => look.id === reel.look);
  return <section aria-label="Look" className="space-y-3">
    <div className="flex items-center gap-3">
      <div role="radiogroup" aria-label="Ratio" className="flex rounded-lg border border-zinc-800 p-0.5">
        {ratios.map(ratio => <button key={ratio} type="button" role="radio" aria-checked={reel.ratio === ratio} onClick={() => onRatio(ratio)}
          className={`rounded-md px-3 py-1.5 text-xs tabular-nums transition-colors ${reel.ratio === ratio ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-200'}`}>{ratio}</button>)}
      </div>
      <Input value={reel.tag ?? ''} onChange={event => onTag(event.target.value.toUpperCase() || null)} maxLength={24}
        aria-label="Label" placeholder={current?.tag ?? 'Label'} className="max-w-[220px]" />
    </div>
    <div role="radiogroup" aria-label="Look" className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {looks.map(look => <button key={look.id} type="button" role="radio" aria-checked={reel.look === look.id} onClick={() => onLook(look.id)}
        className={`flex flex-col items-center gap-2 rounded-xl border p-3 text-xs transition-colors ${reel.look === look.id ? 'border-zinc-300 bg-zinc-900 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600'}`}>
        <LookSketch look={look} />{look.name}
      </button>)}
    </div>
  </section>;
}

/** A thumbnail of each look's composition, so the layouts can be told apart at a glance. */
function LookSketch({ look }: { look: LookInfo }) {
  const photo = '#475569', ink = '#e5e7eb';
  const shapes: Record<ReelLook, JSX.Element> = {
    breaking: <><rect x="0" y="22" width="36" height="7" fill={look.accent} /><rect x="0" y="29" width="36" height="9" fill="#111827" /><rect x="3" y="31" width="22" height="2" fill={ink} /><rect x="0" y="38" width="36" height="3" fill={look.accent} /></>,
    hot: <><rect x="3" y="3" width="10" height="3" rx="1" fill={look.accent} /><rect x="3" y="8" width="28" height="3" fill={ink} /><rect x="3" y="13" width="20" height="3" fill={ink} /><rect x="3" y="33" width="26" height="6" rx="2" fill={look.accent} /></>,
    politics: <><rect x="0" y="0" width="36" height="21" fill={photo} /><rect x="0" y="21" width="36" height="21" fill="#0c0e14" /><rect x="3" y="25" width="8" height="1.5" fill={look.accent} /><rect x="3" y="29" width="28" height="2.5" fill={ink} /><rect x="3" y="34" width="18" height="2" fill="#9ca3af" /></>,
    wire: <><rect x="0" y="0" width="36" height="42" fill={photo} /><rect x="0" y="22" width="36" height="20" fill="#0b0e14" opacity=".8" /><rect x="3" y="23" width="30" height="1.5" fill={look.accent} /><rect x="3" y="27" width="7" height="1.5" fill={look.accent} /><rect x="3" y="31" width="28" height="3" fill={ink} /><rect x="3" y="36" width="18" height="2" fill="#9ca3af" /></>,
    editorial: <><rect x="0" y="0" width="36" height="42" fill="#faf6ee" /><rect x="3" y="3" width="6" height="3" fill={look.accent} /><rect x="3" y="9" width="30" height="3" fill="#111827" /><rect x="3" y="14" width="22" height="3" fill="#111827" /><rect x="3" y="19" width="16" height="1.5" fill="#6b7280" /><rect x="0" y="24" width="36" height="18" fill={photo} /></>,
    clean: <><rect x="0" y="0" width="36" height="42" fill="#f4f6fa" /><rect x="3" y="4" width="30" height="17" rx="3" fill={photo} /><rect x="5" y="19" width="9" height="4" rx="1" fill={look.accent} /><rect x="3" y="27" width="28" height="3" fill="#111827" /><rect x="3" y="33" width="18" height="2" fill="#6b7280" /></>,
  };
  return <svg viewBox="0 0 36 42" className="h-14 w-12 overflow-hidden rounded-md" aria-hidden="true">
    <rect width="36" height="42" fill={photo} />{shapes[look.id]}</svg>;
}
