export function configuredGemini(): { api_key: string; model: string } {
  let saved: { gemini?: string; gemini_model?: string } = {};
  try { saved = JSON.parse(localStorage.getItem('app_api_keys') || '{}'); } catch { /* Report the missing setting below. */ }
  if (!saved || typeof saved.gemini !== 'string' || !saved.gemini.trim()) throw new Error('Add your Gemini API key in Settings first.');
  return { api_key: saved.gemini.trim(), model: saved.gemini_model || 'gemini-3.8-flash' };
}
