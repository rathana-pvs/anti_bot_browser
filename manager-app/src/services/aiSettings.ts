/** AI providers, their keys and which one each Reel Studio task uses. Saved on this computer only. */
export type AIProvider = 'gemini' | 'claude' | 'openai';
export type AITask = 'write' | 'direct';

export interface AISettings {
  gemini: string;
  gemini_model: string;
  claude: string;
  claude_model: string;
  openai: string;
  openai_model: string;
  openai_base_url: string;
  write_provider: AIProvider;
  direct_provider: AIProvider;
}

/** What the backend needs to call one provider. */
export interface AIRequestConfig { provider: AIProvider; api_key: string; model: string; base_url?: string }

export const STORAGE_KEY = 'app_api_keys';

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

export const CLAUDE_MODELS = [
  { value: 'claude-opus-5-5', label: 'Claude Opus 5.5 (best quality)' },
  { value: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5 (balanced)' },
  { value: 'claude-haiku-5-5', label: 'Claude Haiku 5.5 (fastest, lowest cost)' },
];

export const PROVIDERS: { value: AIProvider; label: string }[] = [
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'claude', label: 'Claude' },
  { value: 'openai', label: 'OpenAI-compatible' },
];

export const DEFAULT_SETTINGS: AISettings = {
  gemini: '', gemini_model: 'gemini-3.8-flash', claude: '', claude_model: 'claude-opus-5-5',
  openai: '', openai_model: '', openai_base_url: '', write_provider: 'gemini', direct_provider: 'gemini',
};

const text = (value: unknown) => typeof value === 'string' ? value : '';
const provider = (value: unknown): AIProvider => value === 'claude' || value === 'openai' ? value : 'gemini';
const listed = (models: { value: string }[], value: unknown, fallback: string) => models.some(m => m.value === value) ? value as string : fallback;

export function loadAISettings(): AISettings {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') ?? {};
    return {
      gemini: text(stored.gemini), gemini_model: listed(GEMINI_MODELS, stored.gemini_model, DEFAULT_SETTINGS.gemini_model),
      claude: text(stored.claude), claude_model: listed(CLAUDE_MODELS, stored.claude_model, DEFAULT_SETTINGS.claude_model),
      openai: text(stored.openai), openai_model: text(stored.openai_model), openai_base_url: text(stored.openai_base_url),
      write_provider: provider(stored.write_provider), direct_provider: provider(stored.direct_provider),
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

/** Throws when local storage is unavailable, so the caller can say so. */
export function saveAISettings(settings: AISettings): AISettings {
  const trimmed = { ...settings, gemini: settings.gemini.trim(), claude: settings.claude.trim(), openai: settings.openai.trim(),
    openai_model: settings.openai_model.trim(), openai_base_url: settings.openai_base_url.trim() };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  return trimmed;
}

const isLocal = (url: string) => /^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/|$)/i.test(url.trim());

/** A short name for the model a task uses, e.g. "Claude Sonnet 5.5". */
export function modelLabel(settings: AISettings, task: AITask): string {
  const chosen = settings[task === 'write' ? 'write_provider' : 'direct_provider'];
  if (chosen === 'claude') return (CLAUDE_MODELS.find(m => m.value === settings.claude_model)?.label ?? settings.claude_model).replace(/ \(.*\)$/, '');
  if (chosen === 'openai') return settings.openai_model || 'OpenAI-compatible model';
  return (GEMINI_MODELS.find(m => m.value === settings.gemini_model)?.label ?? settings.gemini_model).replace(/ \(.*\)$/, '');
}

/** What one provider needs from these settings, or a plain error naming what to set first. */
export function providerConfig(settings: AISettings, chosen: AIProvider): AIRequestConfig {
  if (chosen === 'claude') {
    if (!settings.claude.trim()) throw new Error('Add your Claude API key in Settings first.');
    return { provider: 'claude', api_key: settings.claude.trim(), model: settings.claude_model };
  }
  if (chosen === 'openai') {
    if (!settings.openai_model.trim()) throw new Error('Choose a model for the OpenAI-compatible provider in Settings first.');
    if (!settings.openai.trim() && !isLocal(settings.openai_base_url)) throw new Error('Add your OpenAI-compatible API key in Settings first.');
    return { provider: 'openai', api_key: settings.openai.trim(), model: settings.openai_model.trim(), base_url: settings.openai_base_url.trim() };
  }
  if (!settings.gemini.trim()) throw new Error('Add your Gemini API key in Settings first.');
  return { provider: 'gemini', api_key: settings.gemini.trim(), model: settings.gemini_model };
}

/** The provider, key and model a task uses. */
export function configuredAI(task: AITask, settings: AISettings = loadAISettings()): AIRequestConfig {
  return providerConfig(settings, settings[task === 'write' ? 'write_provider' : 'direct_provider']);
}
