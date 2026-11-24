import React, { useEffect, useRef, useState } from 'react';
import { Select } from './ui/Select';
import { AppUpdates } from './AppUpdates';
import { ResourceModeControl } from './ResourceModeControl';
import { AutoWarmingControl } from './AutoWarmingControl';
import { PostingPlanControl } from './PostingPlanControl';
import { StorageControl } from './StorageControl';
import { WebsiteConnectionsSettings } from './WebsiteConnectionsSettings';
import { WebAccessSettings } from './WebAccessSettings';
import { MANAGE_WEBSITE_CONNECTIONS } from '../services/websites';
import { GEMINI_MODELS, MAX_KEYS, loadAISettings, newKeyRow, providerConfig, saveAISettings, type AISettings, type GeminiKey, type GeminiKeyStatus } from '../services/aiSettings';
import { reelApi } from '../services/reelCreator';
import { Check, CircleAlert, Cpu, Eye, EyeOff, Globe, HardDrive, KeyRound, Loader2, MonitorSmartphone, Plus, Settings, Trash2, X } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenSetup?: () => void;
  onRequestOpen?: () => void;
}

type Section = 'api-keys' | 'updates' | 'resources' | 'storage' | 'websites' | 'web-access';

const SECTION_COPY: Record<Section, { title: string; description: string }> = {
  'api-keys': { title: 'AI models', description: 'Your Gemini keys, used in turn: when one hits a limit, the next one takes over.' },
  updates: { title: 'App updates', description: 'Check published versions and update the whole app.' },
  resources: { title: 'Resources', description: 'Control how much automation runs at once and which OCR engine it uses.' },
  websites: { title: 'Websites', description: 'Connect websites once, then select them when posting.' },
  storage: { title: 'Storage', description: 'What uses disk space, and housekeeping that keeps it from growing.' },
  'web-access': { title: 'Web access', description: 'Use this app from your phone or another computer through Tailscale.' },
};

type KeyId = string;

interface KeyFieldProps { id: KeyId; label: string; value: string; placeholder?: string; visible: boolean; onToggle: () => void; onChange: (value: string) => void }

function KeyField({ id, label, value, placeholder = 'API key', visible, onToggle, onChange }: KeyFieldProps) {
  return <label className="block text-xs font-medium text-zinc-300">
    API key
    <div className="relative mt-2">
      <input type={visible ? 'text' : 'password'} aria-label={`${label} API key`} value={value} onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder} autoComplete="off" data-key={id}
        className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 pr-10 text-xs text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-blue-500" />
      <button type="button" onClick={onToggle} aria-label={`${visible ? 'Hide' : 'Show'} ${label} key`} className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-zinc-500 hover:text-zinc-200">
        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  </label>;
}

type CheckState = { state: 'idle' } | { state: 'checking' } | { state: 'ok'; text: string } | { state: 'failed'; text: string };

/** One tiny request in the director's own answer format, with the values on screen (saved or not). */
function ModelCheck({ settings, onlyKey, onChecked }: { settings: AISettings; onlyKey?: string; onChecked?: () => void }) {
  const [check, setCheck] = useState<CheckState>({ state: 'idle' });
  const config = (() => { try { return JSON.stringify(providerConfig(settings, onlyKey)); } catch { return ''; } })();
  useEffect(() => { setCheck({ state: 'idle' }); }, [config]);
  const run = async () => {
    setCheck({ state: 'checking' });
    try {
      const result = await reelApi.checkAI(providerConfig(settings, onlyKey));
      setCheck({ state: 'ok', text: `Works: ${result.model} answered in ${result.seconds.toFixed(1)} s.` });
    } catch (err) {
      setCheck({ state: 'failed', text: err instanceof Error ? err.message : 'The check failed.' });
    }
    onChecked?.();
  };
  return <div className="flex items-start gap-3">
    <button type="button" onClick={() => { void run(); }} disabled={check.state === 'checking'}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-60">
      {check.state === 'checking' && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}Test
    </button>
    <p role="status" aria-live="polite" className={`min-w-0 pt-1.5 text-[11px] leading-4 ${check.state === 'failed' ? 'text-red-400' : check.state === 'ok' ? 'text-emerald-400' : 'text-zinc-500'}`}>
      {check.state === 'checking' ? 'Asking the model…' : check.state === 'ok' ? <span className="inline-flex items-start gap-1"><Check className="mt-px h-3 w-3 shrink-0" />{check.text}</span>
        : check.state === 'failed' ? <span className="inline-flex items-start gap-1"><CircleAlert className="mt-px h-3 w-3 shrink-0" />{check.text}</span>
        : onlyKey !== undefined ? 'Sends one tiny request with this key, even while it rests.' : 'Sends one tiny request to check the key and model.'}
    </p>
  </div>;
}

function waitText(seconds: number | null) {
  if (seconds === null) return '';
  if (seconds < 90) return `${seconds} s`;
  if (seconds < 5400) return `${Math.ceil(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

function KeyStatusBadge({ status }: { status?: GeminiKeyStatus }) {
  if (!status) return null;
  const [text, tone] = status.state === 'ready' ? ['Ready', 'border-emerald-500/30 text-emerald-300']
    : status.state === 'rate' ? [`Cooling down · ${waitText(status.seconds_left)}`, 'border-amber-500/30 text-amber-300']
    : status.state === 'quota' ? [`Daily quota used · back in ${waitText(status.seconds_left)}`, 'border-amber-500/30 text-amber-300']
    : ['Rejected · check this key', 'border-red-500/30 text-red-300'];
  return <span role="status" className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${tone}`}>{text}</span>;
}

interface GeminiKeysProps {
  settings: AISettings; statuses: Record<string, GeminiKeyStatus>; visible: Set<KeyId>;
  onToggle: (id: KeyId) => void; onChange: (keys: GeminiKey[]) => void; onChecked: () => void;
}

/** Every saved Gemini key: a label, the key, what it is doing now, and a test of that key alone. */
function GeminiKeys({ settings, statuses, visible, onToggle, onChange, onChecked }: GeminiKeysProps) {
  const rows = settings.gemini_keys;
  const update = (id: string, patch: Partial<GeminiKey>) => onChange(rows.map(row => row.id === id ? { ...row, ...patch } : row));
  return <div className="space-y-3">
    {rows.length === 0 && <p className="text-[11px] text-zinc-500">No key yet. Add one from Google AI Studio.</p>}
    {rows.map((row, index) => <div key={row.id} className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
      <div className="flex items-center gap-2">
        <input aria-label={`Name of Gemini key ${index + 1}`} value={row.label} onChange={(event) => update(row.id, { label: event.target.value })}
          placeholder={`Key ${index + 1}`} maxLength={40} autoComplete="off"
          className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-1 text-xs font-medium text-zinc-100 outline-none hover:border-zinc-800 focus:border-blue-500" />
        <KeyStatusBadge status={statuses[row.key.trim()]} />
        <button type="button" onClick={() => onChange(rows.filter(other => other.id !== row.id))} aria-label={`Remove ${row.label || `key ${index + 1}`}`}
          className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-red-300"><Trash2 className="h-3.5 w-3.5" /></button>
      </div>
      <KeyField id={row.id} label={row.label || `Gemini key ${index + 1}`} value={row.key} visible={visible.has(row.id)}
        onToggle={() => onToggle(row.id)} onChange={(key) => update(row.id, { key })} />
      {row.key.trim() && <ModelCheck settings={settings} onlyKey={row.key} onChecked={onChecked} />}
    </div>)}
    {rows.length < MAX_KEYS && <button type="button" onClick={() => onChange([...rows, newKeyRow(`Key ${rows.length + 1}`)])}
      className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-900">
      <Plus className="h-3.5 w-3.5" />Add key
    </button>}
  </div>;
}

const card = 'space-y-3 rounded-xl border border-zinc-800 bg-zinc-950/60 p-4';

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose, onOpenSetup, onRequestOpen }) => {
  const [section, setSection] = useState<Section>('api-keys');
  const [apiKeys, setApiKeys] = useState<AISettings>(loadAISettings);
  const [visibleKeys, setVisibleKeys] = useState<Set<KeyId>>(new Set());
  const [keyStatuses, setKeyStatuses] = useState<Record<string, GeminiKeyStatus>>({});
  const [statusTick, setStatusTick] = useState(0);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const savedTimer = useRef<number>();

  useEffect(() => {
    const open = () => { setSection('websites'); onRequestOpen?.(); };
    window.addEventListener(MANAGE_WEBSITE_CONNECTIONS, open);
    return () => window.removeEventListener(MANAGE_WEBSITE_CONNECTIONS, open);
  }, [onRequestOpen]);

  useEffect(() => {
    if (!isOpen) return;
    setApiKeys(loadAISettings());
    setVisibleKeys(new Set());
    setSaved(false);
    setSaveError(null);
    if (savedTimer.current) window.clearTimeout(savedTimer.current);
    // Parent polling changes callback identities; only opening starts a new draft.
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [isOpen, onClose]);

  useEffect(() => () => {
    if (savedTimer.current) window.clearTimeout(savedTimer.current);
  }, []);

  // What each key on screen is doing (ready, cooling down, out of quota, rejected), refreshed while this page is open.
  const draftKeys = [...new Set(apiKeys.gemini_keys.map(row => row.key.trim()).filter(Boolean))];
  const draftKeysKey = draftKeys.join('\n');
  useEffect(() => {
    if (!isOpen || section !== 'api-keys' || !draftKeys.length) { setKeyStatuses({}); return; }
    let live = true;
    const refresh = () => reelApi.keyStatus(draftKeys).then(({ keys }) => {
      if (live) setKeyStatuses(Object.fromEntries(draftKeys.map((key, index) => [key, keys[index]])));
    }).catch(() => { /* The badges stay as they were; calls still report limits themselves. */ });
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { live = false; window.clearInterval(timer); };
    // draftKeysKey stands for draftKeys; statusTick refreshes right after a Test.
  }, [isOpen, section, draftKeysKey, statusTick]);

  if (!isOpen) return null;

  const change = (patch: Partial<AISettings>) => { setApiKeys((current) => ({ ...current, ...patch })); setSaved(false); };
  const toggle = (id: KeyId) => setVisibleKeys((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const saveKeys = () => {
    try {
      setApiKeys(saveAISettings(apiKeys));
      setSaveError(null);
      setSaved(true);
      if (savedTimer.current) window.clearTimeout(savedTimer.current);
      savedTimer.current = window.setTimeout(() => setSaved(false), 1800);
    } catch {
      setSaved(false);
      setSaveError('Settings could not be saved on this computer. Check that local storage is available and try again.');
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/75 p-5 backdrop-blur-sm" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onMouseDown={(event) => event.stopPropagation()}
        className="flex h-[min(640px,88vh)] w-full max-w-3xl overflow-hidden rounded-2xl border border-zinc-800 bg-[#0b0c0f] shadow-2xl"
      >
        <aside className="w-52 shrink-0 border-r border-zinc-800 bg-zinc-950/80 p-3">
          <div className="mb-5 flex items-center gap-2 px-2 pt-2">
            <Settings className="h-4 w-4 text-zinc-300" />
            <h2 id="settings-title" className="text-sm font-semibold text-white">Settings</h2>
          </div>
          <nav className="space-y-1">
            <button type="button" onClick={() => setSection('updates')} className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${section === 'updates' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
              <Settings className="h-4 w-4" /> App updates
            </button>
            <button type="button" onClick={() => setSection('api-keys')} className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${section === 'api-keys' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
              <KeyRound className="h-4 w-4" /> AI models
            </button>
            <button type="button" onClick={() => setSection('websites')} className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${section === 'websites' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
              <Globe className="h-4 w-4" /> Websites
            </button>
            <button type="button" onClick={() => setSection('resources')} className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${section === 'resources' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
              <Cpu className="h-4 w-4" /> Resources
            </button>
            <button type="button" onClick={() => setSection('storage')} className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${section === 'storage' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
              <HardDrive className="h-4 w-4" /> Storage
            </button>
            <button type="button" onClick={() => setSection('web-access')} className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${section === 'web-access' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
              <MonitorSmartphone className="h-4 w-4" /> Web access
            </button>
            {onOpenSetup && <button type="button" onClick={onOpenSetup} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200">
              <Settings className="h-4 w-4" /> Setup and repair
            </button>}
          </nav>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800 px-6">
            <div>
              <h3 className="text-sm font-semibold text-zinc-100">{SECTION_COPY[section].title}</h3>
              <p className="mt-0.5 text-[11px] text-zinc-500">{SECTION_COPY[section].description}</p>
            </div>
            <button type="button" onClick={onClose} aria-label="Close settings" className="rounded-md p-2 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"><X className="h-4 w-4" /></button>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto p-6">
            {section === 'api-keys' ? (
              <div className="max-w-xl space-y-5">
                <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs leading-5 text-amber-200/80">
                  Your keys and model choice are saved on this computer. Keys are sent only to Google, when you use them.
                </div>
                <div className={card}>
                  <div>
                    <p className="text-xs font-semibold text-zinc-100">Google Gemini keys</p>
                    <p className="mt-1 text-[11px] leading-5 text-zinc-500">Keys take turns. When one hits a rate limit or its daily quota, or Google rejects it, the same request moves on to the next key, and that key rests until it can answer again.</p>
                  </div>
                  <GeminiKeys settings={apiKeys} statuses={keyStatuses} visible={visibleKeys} onToggle={toggle}
                    onChange={(gemini_keys) => change({ gemini_keys })} onChecked={() => setStatusTick((tick) => tick + 1)} />
                </div>
                <div className={card}>
                  <p className="text-xs font-semibold text-zinc-100">Model</p>
                  <Select ariaLabel="Gemini model version" value={apiKeys.gemini_model} options={GEMINI_MODELS} onValueChange={(gemini_model) => change({ gemini_model })} />
                  <p className="text-[11px] text-zinc-500">Every key uses this model. Model availability depends on your API key. Legacy models require existing access.</p>
                </div>
                {saveError && <p role="alert" className="text-xs text-red-400">{saveError}</p>}
                <div className="flex items-center justify-end gap-3 pt-2">
                  {saved && <span role="status" className="flex items-center gap-1.5 text-xs text-emerald-400"><Check className="h-3.5 w-3.5" />Saved</span>}
                  <button type="button" onClick={saveKeys} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500">Save settings</button>
                </div>
              </div>
            ) : section === 'websites' ? <WebsiteConnectionsSettings /> : section === 'resources' ? (
              <div className="max-w-xl space-y-4">
                <p className="text-xs leading-5 text-zinc-400">Resource mode sets how many browsers can run, warm up, and post at the same time. Auto picks a level from this computer’s memory and CPU.</p>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4"><ResourceModeControl /></div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4"><AutoWarmingControl /></div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4"><PostingPlanControl /></div>
              </div>
            ) : section === 'storage' ? (
              <div className="max-w-xl"><StorageControl /></div>
            ) : section === 'web-access' ? <WebAccessSettings /> : <AppUpdates />}
          </div>
        </section>
      </div>
    </div>
  );
};
