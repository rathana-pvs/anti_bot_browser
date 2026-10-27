import { useRef } from 'react';
import { RotateCcw, X } from 'lucide-react';
import { Checkbox } from '../ui/Checkbox';
import { Select } from '../ui/Select';
import { effectiveStyle, sceneIsStyled, sceneSeconds } from '../../services/reelDocument';
import type { ElementStyle, LookInfo, Reel, ReelLook, ReelScene, SceneLayout, StyleRole } from '../../types/reelCreator';

interface Props {
  reel: Reel;
  looks: LookInfo[];
  /** The scene being edited when `scope` is 'scene'. */
  scene: number;
  scope: 'reel' | 'scene';
  onScope: (scope: 'reel' | 'scene') => void;
  role: StyleRole;
  onRole: (role: StyleRole) => void;
  onStyle: (role: StyleRole, patch: Partial<Record<keyof ElementStyle, unknown>> | null) => void;
  onScene: (patch: Partial<ReelScene>) => void;
  onAccent: (accent: string | null) => void;
  onResetScene: () => void;
  /** The selected scene's preview layout: whether, and from where, its text can move. */
  layout: SceneLayout | null;
}

type Field = 'font' | 'scale' | 'color' | 'background' | 'animation' | 'hidden';
/** Select value for "keep the look's (or reel's) own"; Select options cannot use an empty value. */
const LOOK = 'look';
const unset = (value: string) => value === LOOK ? null : value;

/** What each role can change; the renderer ignores anything else. */
const roles: { id: StyleRole; name: string; fields: Field[] }[] = [
  { id: 'headline', name: 'Headline', fields: ['font', 'scale', 'color', 'background', 'animation', 'hidden'] },
  { id: 'detail', name: 'Detail', fields: ['font', 'scale', 'color', 'background', 'animation', 'hidden'] },
  { id: 'lead', name: 'Figure / mark', fields: ['color', 'animation', 'hidden'] },
  { id: 'label', name: 'Label', fields: ['font', 'color', 'background', 'animation', 'hidden'] },
  { id: 'credit', name: 'Photo credit', fields: ['color', 'hidden'] },
  { id: 'brand', name: 'Brand', fields: ['font', 'color', 'animation', 'hidden'] },
  { id: 'logo', name: 'Logo', fields: ['animation', 'hidden'] },
  { id: 'ticker', name: 'Ticker', fields: ['color', 'background', 'hidden'] },
];
/** The brand row and the ticker are drawn once for the whole reel. */
export const reelOnly: StyleRole[] = ['brand', 'logo', 'ticker'];
export const roleName = (role: StyleRole) => roles.find(r => r.id === role)?.name ?? role;
const fonts = [
  { value: LOOK, label: 'Look font' }, { value: 'bold', label: 'Sans bold' }, { value: 'regular', label: 'Sans' },
  { value: 'display', label: 'Poster' }, { value: 'condensed', label: 'Condensed' }, { value: 'condensed-semi', label: 'Condensed light' },
  { value: 'serif', label: 'Serif bold' }, { value: 'serif-italic', label: 'Serif italic' },
];
const animations = [
  { value: LOOK, label: 'Look entrance' }, { value: 'fade', label: 'Fade' }, { value: 'rise', label: 'Rise' }, { value: 'left', label: 'Slide from left' },
  { value: 'pop', label: 'Pop' }, { value: 'wipe', label: 'Wipe' }, { value: 'none', label: 'Appear' },
];
const motions = [{ value: LOOK, label: 'Reel motion' }, { value: 'zoom_in', label: 'Zoom in' }, { value: 'zoom_out', label: 'Zoom out' }, { value: 'none', label: 'Still' }];
const transitions = [{ value: LOOK, label: 'Crossfade' }, { value: 'slide', label: 'Slide' }, { value: 'cut', label: 'Cut' }];
/** Looks whose label is plain text, with no strap, pill, chip or tab to fill. */
const unfilledLabel: ReelLook[] = ['politics', 'wire'];
/** Looks that darken the photo behind their text. */
const shaded: ReelLook[] = ['breaking', 'hot', 'wire', 'politics'];

/** Advanced mode: per-element font, color, background and entrance, for the whole reel or one scene. */
export function StylePanel(p: Props) {
  const current = p.reel.scenes[p.scene];
  const visible = roles.filter(r => r.id !== 'ticker' || p.reel.look === 'breaking');
  const role = visible.find(r => r.id === p.role) ?? visible[0];
  const scope = reelOnly.includes(role.id) ? 'reel' : p.scope;
  const inherited = p.reel.style?.[role.id] ?? {};
  const own = scope === 'reel' ? inherited : current?.style?.[role.id] ?? {};
  const shown = scope === 'reel' ? inherited : effectiveStyle(p.reel, current, role.id);
  const set = (patch: Partial<Record<keyof ElementStyle, unknown>>) => p.onStyle(role.id, patch);
  const look = p.looks.find(l => l.id === p.reel.look);
  const has = (field: Field) => role.fields.includes(field) && !(field === 'background' && role.id === 'label' && unfilledLabel.includes(p.reel.look));
  const styledRoles = (id: StyleRole) => !!(p.reel.style?.[id] || (scope === 'scene' && current?.style?.[id]));

  return <section id="reel-style" aria-label="Style" className="scroll-mt-4 space-y-4 rounded-xl border border-zinc-800 p-4">
    <div className="flex flex-wrap items-center gap-3">
      <h2 className="text-sm font-medium text-zinc-100">Style</h2>
      <div role="radiogroup" aria-label="Apply to" className="flex rounded-lg border border-zinc-800 p-0.5">
        {([['reel', 'All scenes'], ['scene', `Scene ${p.scene + 1}`]] as const).map(([value, label]) =>
          <button key={value} type="button" role="radio" aria-checked={p.scope === value} onClick={() => p.onScope(value)}
            className={`rounded-md px-3 py-1.5 text-xs transition-colors ${p.scope === value ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-200'}`}>{label}</button>)}
      </div>
      {p.scope === 'reel'
        ? <ColorField label="Accent" value={p.reel.accent} fallback={look?.accent} onChange={p.onAccent} />
        : current && sceneIsStyled(current) && <button type="button" onClick={p.onResetScene}
          className="ml-auto flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-200"><RotateCcw className="h-3 w-3" />Reset scene style</button>}
    </div>

    <div role="radiogroup" aria-label="Element" className="flex flex-wrap gap-1.5">
      {visible.map(r => <button key={r.id} type="button" role="radio" aria-checked={role.id === r.id} onClick={() => p.onRole(r.id)}
        className={`relative rounded-full border px-3 py-1 text-xs transition-colors ${role.id === r.id ? 'border-violet-500 bg-violet-950/40 text-violet-100' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'}`}>
        {r.name}{styledRoles(r.id) && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-violet-400" aria-label="changed" />}
      </button>)}
    </div>

    {reelOnly.includes(role.id) && p.scope === 'scene' && <p className="text-xs text-zinc-500">{role.name} is shown for the whole reel, so it is styled for all scenes.</p>}
    {role.id === 'lead' && <p className="text-xs text-zinc-500">The big figure, quotation mark, or the ? or → on an end card.</p>}

    <div className="grid gap-3 sm:grid-cols-2">
      {has('font') && <Row label="Font"><Select value={shown.font ?? LOOK} options={fonts} ariaLabel={`${role.name} font`}
        onValueChange={value => set({ font: unset(value) })} /></Row>}
      {has('scale') && <Row label="Size" note={`${Math.round((shown.scale ?? 1) * 100)}%`}>
        <input type="range" min={70} max={130} step={5} value={Math.round((shown.scale ?? 1) * 100)} aria-label={`${role.name} size`}
          onChange={event => set({ scale: Number(event.target.value) === Math.round((scope === 'scene' ? inherited.scale ?? 1 : 1) * 100) ? null : Number(event.target.value) / 100 })} className="w-full accent-violet-500" /></Row>}
      {has('color') && <Row label="Color"><ColorField label={`${role.name} color`} value={shown.color ?? null} onChange={color => set({ color })} /></Row>}
      {has('background') && <Row label={role.id === 'headline' || role.id === 'detail' ? 'Box behind text' : 'Fill'}>
        <ColorField label={`${role.name} background`} value={shown.background ?? null} onChange={background => set({ background })} /></Row>}
      {has('animation') && <Row label="Entrance"><Select value={shown.animation ?? LOOK} options={animations} ariaLabel={`${role.name} entrance`}
        onValueChange={value => set({ animation: unset(value) })} /></Row>}
    </div>

    <div className="flex items-center gap-4">
      <label className="flex items-center gap-2 text-xs text-zinc-300">
        <Checkbox checked={!!shown.hidden} onCheckedChange={hidden => set({ hidden: hidden || (scope === 'scene' && inherited.hidden ? false : null) })} />Hide {role.name.toLowerCase()}</label>
      {Object.keys(own).length > 0 && <button type="button" onClick={() => p.onStyle(role.id, null)}
        className="ml-auto flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-200"><RotateCcw className="h-3 w-3" />
        {scope === 'scene' ? 'Use the reel style' : `Reset ${role.name.toLowerCase()}`}</button>}
    </div>
    {p.scene === 0 && scope === 'scene' && has('animation') && <p className="text-xs text-zinc-500">The first scene opens fully drawn, so its entrances are not played.</p>}

    {scope === 'scene' && current && <SceneBackdrop reel={p.reel} scene={current} index={p.scene} onScene={p.onScene} layout={p.layout} />}
  </section>;
}

function SceneBackdrop({ reel, scene, index, onScene, layout }: { reel: Reel; scene: ReelScene; index: number; onScene: (patch: Partial<ReelScene>) => void; layout: SceneLayout | null }) {
  const position = scene.text_position ?? layout?.position;
  return <div className="grid gap-3 border-t border-zinc-800 pt-4 sm:grid-cols-2">
    {layout?.position != null && position != null && <Row label="Text position" note={scene.text_position == null ? 'Look' : `${Math.round(position * 100)}% down`}>
      <div className="flex items-center gap-3">
        <span className="text-[11px] text-zinc-500">Higher</span>
        <input type="range" min={0} max={100} step={1} value={Math.round(position * 100)} aria-label="Text position"
          onChange={event => onScene({ text_position: Number(event.target.value) / 100 })} className="w-full accent-violet-500" />
        <span className="text-[11px] text-zinc-500">Lower</span>
        {scene.text_position != null && <button type="button" aria-label="Put the text back where the look puts it" title="Back to the look's place"
          onClick={() => onScene({ text_position: null })} className="text-zinc-500 hover:text-zinc-200"><RotateCcw className="h-3 w-3" /></button>}
      </div>
    </Row>}
    {layout?.photo_size != null && <Row label="Photo size" note={scene.photo_size == null ? 'Look' : `${Math.round(scene.photo_size * 100)}%`}>
      <div className="flex items-center gap-3">
        <span className="text-[11px] text-zinc-500">Smaller</span>
        <input type="range" min={0} max={100} step={1} value={Math.round((scene.photo_size ?? layout.photo_size) * 100)} aria-label="Photo size"
          onChange={event => onScene({ photo_size: Number(event.target.value) / 100 })} className="w-full accent-violet-500" />
        <span className="text-[11px] text-zinc-500">Larger</span>
        {scene.photo_size != null && <button type="button" aria-label="Size the photo to the text again" title="Back to the look's size"
          onClick={() => onScene({ photo_size: null })} className="text-zinc-500 hover:text-zinc-200"><RotateCcw className="h-3 w-3" /></button>}
      </div>
    </Row>}
    {layout?.photo && <Row label="Photo zoom" note={`${Math.round((scene.zoom ?? 1) * 100)}%`}>
      <div className="flex items-center gap-3">
        <input type="range" min={100} max={250} step={5} value={Math.round((scene.zoom ?? 1) * 100)} aria-label="Photo zoom"
          onChange={event => onScene({ zoom: Number(event.target.value) / 100 })} className="w-full accent-violet-500" />
        {(scene.zoom ?? 1) !== 1 && <button type="button" aria-label="Show the whole crop again" title="Back to 100%"
          onClick={() => onScene({ zoom: 1 })} className="text-zinc-500 hover:text-zinc-200"><RotateCcw className="h-3 w-3" /></button>}
      </div>
    </Row>}
    <Row label="Photo motion"><Select value={scene.motion ?? LOOK} options={motions} ariaLabel="Scene photo motion"
      onValueChange={value => onScene({ motion: unset(value) as ReelScene['motion'] })} /></Row>
    <Row label="Comes in with"><Select value={scene.transition ?? LOOK} options={transitions} ariaLabel="Scene transition" disabled={index === 0}
      onValueChange={value => onScene({ transition: unset(value) as ReelScene['transition'] })} /></Row>
    {shaded.includes(reel.look) && <Row label="Shade behind text" note={`${Math.round((scene.shade ?? 1) * 100)}%`}>
      <input type="range" min={0} max={200} step={10} value={Math.round((scene.shade ?? 1) * 100)} aria-label="Shade behind text"
        onChange={event => onScene({ shade: Number(event.target.value) === 100 ? null : Number(event.target.value) / 100 })} className="w-full accent-violet-500" /></Row>}
    {!scene.image_id && <Row label="Background"><ColorField label="Scene background" value={scene.backdrop ?? null} onChange={backdrop => onScene({ backdrop })} /></Row>}
    <Row label="Length" note={scene.seconds == null ? `Auto · ${sceneSeconds(scene)}s` : `${scene.seconds}s`}>
      <div className="flex items-center gap-3">
        <input type="range" min={2} max={20} step={.5} value={sceneSeconds(scene)} aria-label="Scene length" disabled={scene.seconds == null}
          onChange={event => onScene({ seconds: Number(event.target.value) })} className="w-full accent-violet-500 disabled:opacity-40" />
        <label className="flex shrink-0 items-center gap-2 text-xs text-zinc-300">
          <Checkbox checked={scene.seconds == null} onCheckedChange={auto => onScene({ seconds: auto ? null : sceneSeconds(scene) })} />Auto</label>
      </div>
    </Row>
  </div>;
}

function Row({ label, note, children }: { label: string; note?: string; children: React.ReactNode }) {
  return <div className="space-y-1.5">
    <div className="flex justify-between text-xs"><span className="text-zinc-400">{label}</span>{note && <span className="tabular-nums text-zinc-500">{note}</span>}</div>
    {children}
  </div>;
}

/** A swatch that opens the system color picker; cleared, the look's own color applies. */
function ColorField({ label, value, fallback, onChange }: { label: string; value: string | null; fallback?: string; onChange: (value: string | null) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return <div className="flex items-center gap-2">
    <button type="button" onClick={() => input.current?.click()} aria-label={label} title={label}
      className="relative flex h-9 items-center gap-2 rounded-lg border border-zinc-800 px-2 text-xs text-zinc-300 hover:border-zinc-600">
      <span className={`h-5 w-5 rounded ${value || fallback ? '' : 'bg-[repeating-linear-gradient(45deg,#3f3f46_0_3px,transparent_3px_6px)]'}`}
        style={value || fallback ? { background: value ?? fallback } : undefined} />
      <span className="tabular-nums">{value ?? (fallback ? `Look ${fallback}` : 'Look')}</span>
      <input ref={input} type="color" tabIndex={-1} aria-hidden="true" value={value ?? fallback ?? '#ffffff'} onChange={event => onChange(event.target.value)}
        className="pointer-events-none absolute inset-0 h-full w-full opacity-0" />
    </button>
    {value && <button type="button" aria-label={`Clear ${label.toLowerCase()}`} onClick={() => onChange(null)} className="text-zinc-500 hover:text-zinc-200"><X className="h-3.5 w-3.5" /></button>}
  </div>;
}
