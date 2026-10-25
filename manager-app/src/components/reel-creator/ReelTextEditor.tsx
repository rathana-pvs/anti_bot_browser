import type { ReelComposition } from '../../types/reelCreator';
import { Checkbox } from '../ui/Checkbox';
import { Select } from '../ui/Select';

const field = 'w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10';

export function ReelTextEditor({ value, onChange }: {
  value: ReelComposition;
  onChange: (changes: Partial<ReelComposition>) => void;
}) {
  return <section aria-label="Video text" className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
    <h2 className="text-sm font-semibold text-zinc-100">Video text</h2>
    {value.photos.some(photo=>photo.headline != null) && <p className="text-xs text-zinc-500">Scenes have their own headlines. Edit them in Media; the shared headline below is used for photos without a custom title.</p>}
    <label className="block space-y-2 text-xs text-zinc-400">Headline<textarea className={`${field} min-h-24 resize-y leading-relaxed`} value={value.headline} maxLength={240} placeholder="Your headline, or leave blank to keep the text in your photos" onChange={event => onChange({ headline: event.target.value })} /></label>
    <label className="block space-y-2 text-xs text-zinc-400">Headline size<Select
      ariaLabel="Headline size"
      value={value.headline_size ?? 'auto'}
      options={[
        { value: 'auto', label: 'Auto · responsive fit' },
        { value: 'small', label: 'Small · compact (60px)' },
        { value: 'medium', label: 'Medium · standard (70px)' },
        { value: 'large', label: 'Large · punchy (80px)' },
        { value: 'xlarge', label: 'Extra Large · bold (90px)' },
      ]}
      onValueChange={headline_size => onChange({ headline_size: headline_size as ReelComposition['headline_size'] })}
    /></label>
    {value.news_design === 'clean' && <details><summary className="cursor-pointer text-xs text-zinc-500">Headline accent</summary><label className="mt-3 block space-y-2 text-xs text-zinc-400">Words to highlight<input className={field} value={value.headline_emphasis || ''} maxLength={60} placeholder="Copy a short phrase from your headline" onChange={event => onChange({ headline_emphasis: event.target.value })}/></label>{value.headline_emphasis && !value.headline.toLocaleLowerCase().includes(value.headline_emphasis.toLocaleLowerCase()) && <p className="mt-2 text-[11px] text-amber-300">This phrase is not in the headline. It will stay unhighlighted.</p>}</details>}
    <label className="flex items-center gap-2 text-xs text-zinc-300"><Checkbox checked={value.show_subtitle} ariaLabel="Show subtitle" onCheckedChange={show_subtitle => onChange({ show_subtitle })} />Show subtitle</label>
    {value.show_subtitle ? <div className="space-y-3">
      <label className="block space-y-2 text-xs text-zinc-400">Subtitle / highlights<textarea className={`${field} resize-y text-xs`} rows={2} value={value.supporting} maxLength={200} placeholder="One or two supporting lines" onChange={event => onChange({ supporting: event.target.value })} /></label>
      <label className="block space-y-2 text-xs text-zinc-400">Subtitle size<Select
        ariaLabel="Subtitle size"
        value={value.subtitle_size ?? 'auto'}
        options={[
          { value: 'auto', label: 'Auto · responsive fit' },
          { value: 'small', label: 'Small · compact (34px)' },
          { value: 'medium', label: 'Medium · standard (40px)' },
          { value: 'large', label: 'Large · prominent (48px)' },
        ]}
        onValueChange={subtitle_size => onChange({ subtitle_size: subtitle_size as ReelComposition['subtitle_size'] })}
      /></label>
    </div> : null}
  </section>;
}
