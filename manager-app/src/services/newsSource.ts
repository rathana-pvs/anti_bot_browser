import type { EditorialDraft } from '../types/reelCreator';

export async function newsSourceFingerprint(value: Pick<EditorialDraft, 'text' | 'source_url' | 'language'>): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify([value.text, value.source_url, value.language]));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('');
}

export function configuredGemini(): { api_key: string; model: string } {
  let saved: { gemini?: string; gemini_model?: string } = {};
  try { saved = JSON.parse(localStorage.getItem('app_api_keys') || '{}'); } catch { /* Report the missing setting below. */ }
  if (!saved || typeof saved.gemini !== 'string' || !saved.gemini.trim()) throw new Error('Add your Gemini API key in Settings first.');
  return { api_key: saved.gemini.trim(), model: saved.gemini_model || 'gemini-3.8-flash' };
}
