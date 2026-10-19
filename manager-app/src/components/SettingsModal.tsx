import React, { useEffect, useRef, useState } from 'react';
import { Select } from './ui/Select';
import { AppUpdates } from './AppUpdates';
import { Check, Eye, EyeOff, KeyRound, Settings, X } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenSetup?: () => void;
}

type Section = 'api-keys' | 'updates';

const API_KEY_FIELDS = [
  { id: 'gemini', label: 'Google Gemini', placeholder: 'API key' },
] as const;

type ApiKeyId = typeof API_KEY_FIELDS[number]['id'];
type ApiKeys = Record<ApiKeyId, string> & { gemini_model: string };

const GEMINI_MODELS = [
  { value: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash' },
  { value: 'gemini-3.7-flash', label: 'Gemini 3.7 Flash' },
  { value: 'gemini-3.6-flash', label: 'Gemini 3.6 Flash' },
  { value: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
  { value: 'gemini-3.5-flash-lite', label: 'Gemini 3.5 Flash-Lite' },
  { value: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash-Lite' },
  { value: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro (Preview)' },
  { value: 'gemini-3-flash-preview', label: 'Gemini 3 Flash (Preview)' },
  { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash (Legacy)' },
  { value: 'gemini-2.5-flash-lite', label: 'Gemini 2.5 Flash-Lite (Legacy)' },
  { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro (Legacy)' },
];

const STORAGE_KEY = 'app_api_keys';
const EMPTY_KEYS: ApiKeys = { gemini: '', gemini_model: 'gemini-3.8-flash' };

function loadApiKeys(): ApiKeys {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return {
      gemini: typeof stored?.gemini === 'string' ? stored.gemini : '',
      gemini_model: GEMINI_MODELS.some((model) => model.value === stored?.gemini_model)
        ? stored.gemini_model : EMPTY_KEYS.gemini_model,
    };
  } catch {
    return EMPTY_KEYS;
  }
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose, onOpenSetup }) => {
  const [section, setSection] = useState<Section>('api-keys');
  const [apiKeys, setApiKeys] = useState<ApiKeys>(loadApiKeys);
  const [visibleKeys, setVisibleKeys] = useState<Set<ApiKeyId>>(new Set());
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const savedTimer = useRef<number>();

  useEffect(() => {
    if (!isOpen) return;
    setApiKeys(loadApiKeys());
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

  if (!isOpen) return null;

  const saveKeys = () => {
    try {
      const normalized = { ...apiKeys, gemini: apiKeys.gemini.trim() };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
      setApiKeys(normalized);
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
              <KeyRound className="h-4 w-4" /> API keys
            </button>
            {onOpenSetup && <button type="button" onClick={onOpenSetup} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200">
              <Settings className="h-4 w-4" /> Setup and repair
            </button>}
          </nav>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800 px-6">
            <div>
              <h3 className="text-sm font-semibold text-zinc-100">{section === 'api-keys' ? 'API keys' : 'App updates'}</h3>
              <p className="mt-0.5 text-[11px] text-zinc-500">{section === 'api-keys' ? 'Manage your Gemini API key and model.' : 'Check published versions and update the whole app.'}</p>
            </div>
            <button type="button" onClick={onClose} aria-label="Close settings" className="rounded-md p-2 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"><X className="h-4 w-4" /></button>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto p-6">
            {section === 'api-keys' ? (
              <div className="max-w-xl space-y-5">
                <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs leading-5 text-amber-200/80">
                  Your Gemini key and model selection are saved on this computer.
                </div>
                {API_KEY_FIELDS.map((field) => {
                  const visible = visibleKeys.has(field.id);
                  return <label key={field.id} className="block text-xs font-medium text-zinc-300">
                    {field.label}
                    <div className="relative mt-2">
                      <input
                        type={visible ? 'text' : 'password'}
                        aria-label={`${field.label} API key`}
                        value={apiKeys[field.id]}
                        onChange={(event) => { setApiKeys((current) => ({ ...current, [field.id]: event.target.value })); setSaved(false); }}
                        placeholder={field.placeholder}
                        autoComplete="off"
                        className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 pr-10 text-xs text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-blue-500"
                      />
                      <button type="button" onClick={() => setVisibleKeys((current) => { const next = new Set(current); if (next.has(field.id)) next.delete(field.id); else next.add(field.id); return next; })} aria-label={`${visible ? 'Hide' : 'Show'} ${field.label} key`} className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-zinc-500 hover:text-zinc-200">
                        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                  </label>;
                })}
                <div className="space-y-2">
                  <p className="text-xs font-medium text-zinc-300">Model version</p>
                  <Select
                    ariaLabel="Gemini model version"
                    value={apiKeys.gemini_model}
                    options={GEMINI_MODELS}
                    onValueChange={(model) => { setApiKeys((current) => ({ ...current, gemini_model: model })); setSaved(false); }}
                  />
                  <p className="text-[11px] text-zinc-500">Model availability depends on your API key. Legacy models require existing access.</p>
                </div>
                {saveError && <p role="alert" className="text-xs text-red-400">{saveError}</p>}
                <div className="flex items-center justify-end gap-3 pt-2">
                  {saved && <span role="status" className="flex items-center gap-1.5 text-xs text-emerald-400"><Check className="h-3.5 w-3.5" />Saved</span>}
                  <button type="button" onClick={saveKeys} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500">Save settings</button>
                </div>
              </div>
            ) : <AppUpdates />}
          </div>
        </section>
      </div>
    </div>
  );
};
