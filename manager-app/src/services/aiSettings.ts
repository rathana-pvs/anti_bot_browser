/** The Gemini keys and model Reel Studio uses. Saved on this computer only. */
export type AITask = 'write' | 'direct';

/** One saved key. `id` only tells rows apart on screen; it is never sent anywhere. */
export interface GeminiKey { id: string; label: string; key: string }

export interface AISettings {
  gemini_keys: GeminiKey[];
  gemini_model: string;
}

/** What the backend needs to call Gemini: every key, used in turn when one hits a limit. */
export interface AIRequestConfig { provider: 'gemini'; api_key: string; api_keys: string[]; model: string }

/** What one key is doing, as the backend last saw it. `seconds_left` is null when ready or rejected. */
export interface GeminiKeyStatus { state: 'ready' | 'rate' | 'quota' | 'rejected'; seconds_left: number | null }

export const STORAGE_KEY = 'app_api_keys';
export const MAX_KEYS = 20;

export const GEMINI_MODELS = [
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

let nextId = 0;
export const newKeyRow = (label: string, key = ''): GeminiKey => ({ id: `key-${Date.now()}-${nextId++}`, label, key });

export const DEFAULT_SETTINGS: AISettings = { gemini_keys: [], gemini_model: 'gemini-3.8-flash' };

const text = (value: unknown) => typeof value === 'string' ? value : '';
const listed = (models: { value: string }[], value: unknown, fallback: string) => models.some(m => m.value === value) ? value as string : fallback;

/** Saved keys, oldest first. A single key saved by an older version becomes "Key 1". */
function storedKeys(stored: Record<string, unknown>): GeminiKey[] {
  if (Array.isArray(stored.gemini_keys)) {
    return stored.gemini_keys.slice(0, MAX_KEYS).flatMap((item: unknown, index) => {
      const value = item && typeof item === 'object' ? item as Record<string, unknown> : {};
      const key = text(value.key).trim();
      return key ? [newKeyRow(text(value.label).trim() || `Key ${index + 1}`, key)] : [];
    });
  }
  const legacy = text(stored.gemini).trim();
  return legacy ? [newKeyRow('Key 1', legacy)] : [];
}

export function loadAISettings(): AISettings {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') ?? {};
    return { gemini_keys: storedKeys(stored), gemini_model: listed(GEMINI_MODELS, stored.gemini_model, DEFAULT_SETTINGS.gemini_model) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

/** Throws when local storage is unavailable, so the caller can say so. Blank rows are dropped. */
export function saveAISettings(settings: AISettings): AISettings {
  const keys = settings.gemini_keys.map(row => ({ ...row, label: row.label.trim(), key: row.key.trim() })).filter(row => row.key).slice(0, MAX_KEYS)
    .map((row, index) => ({ ...row, label: row.label || `Key ${index + 1}` }));
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ gemini_keys: keys.map(({ label, key }) => ({ label, key })), gemini_model: settings.gemini_model }));
  return { gemini_keys: keys, gemini_model: settings.gemini_model };
}

/** A short name for the model, e.g. "Gemini 3.8 Flash". Every task uses the same one. */
export function modelLabel(settings: AISettings, _task?: AITask): string {
  return (GEMINI_MODELS.find(m => m.value === settings.gemini_model)?.label ?? settings.gemini_model).replace(/ \(.*\)$/, '');
}

const usableKeys = (settings: AISettings) => [...new Set(settings.gemini_keys.map(row => row.key.trim()).filter(Boolean))];

/** What Gemini needs from these settings, or a plain error naming what to set first. */
export function providerConfig(settings: AISettings, onlyKey?: string): AIRequestConfig {
  const keys = onlyKey !== undefined ? usableKeys({ ...settings, gemini_keys: [newKeyRow('', onlyKey)] }) : usableKeys(settings);
  if (!keys.length) throw new Error('Add your Gemini API key in Settings first.');
  return { provider: 'gemini', api_key: keys[0], api_keys: keys, model: settings.gemini_model };
}

/** The keys and model a task uses. */
export function configuredAI(_task?: AITask, settings: AISettings = loadAISettings()): AIRequestConfig {
  return providerConfig(settings);
}

/** The same fields without throwing, for callers that let the backend say a key is missing. */
export function geminiFields(settings: AISettings = loadAISettings()): Omit<AIRequestConfig, 'provider'> {
  const keys = usableKeys(settings);
  return { api_key: keys[0] ?? '', api_keys: keys, model: settings.gemini_model };
}
