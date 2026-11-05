import { API_BASE } from './api';

export interface WebsiteField { label?: string; type: 'string' | 'number' | 'boolean'; required?: boolean; maxLength?: number; default?: string | number | boolean | null }
export interface WebsiteSchema { authentication: { type: 'json-login' | 'basic' | 'bearer' }; fields: Record<string, WebsiteField> }
export interface WebsiteConnection { id: string; name: string; baseUrl: string; adapter: string; schema?: WebsiteSchema | null }
export interface WebsiteArticle {
  id: string; title: string; excerpt: string; content: string; cover_file: string; cover_id: string; alt: string;
  category: string; author: string; tags: string; credit: string; slug: string; seo_title: string; seo_description: string;
  custom_fields?: Record<string, string | number | boolean | null>;
}
export interface WebsitePreparation {
  id: string; ready: boolean; article: { id: string; url: string; status: string };
  destinations: Record<string, { caption: string; link: string }>;
}
export interface ReadMoreDraft {
  enabled: boolean; connectionId: string; article: WebsiteArticle; uniqueCaptions: boolean;
  requestId?: string; requestFingerprint?: string; preparedFingerprint?: string;
  preparation?: WebsitePreparation; captions?: Record<string, string>;
  /** The text the saved captions were written from. */
  captionSource?: string;
  /** Unique captions prepared without a website, and what they were prepared for. */
  captionsPreparationId?: string; captionsFor?: string;
}
export const emptyArticle = (): WebsiteArticle => ({ id: '', title: '', excerpt: '', content: '', cover_file: '', cover_id: '', alt: '', category: '', author: '', tags: '', credit: '', slug: '', seo_title: '', seo_description: '' });
export function readMoreFingerprint(value: ReadMoreDraft, type: string, media: string, caption: string, profiles: string[]) {
  // Unique captions replace the supplied caption, so editing it changes nothing.
  return JSON.stringify([value.connectionId, value.article, value.uniqueCaptions, type, media, value.uniqueCaptions ? '' : caption, [...profiles].sort()]);
}
/** Captions are written from the article when there is one, otherwise from the post's caption. */
export const captionSourceOf = (article: WebsiteArticle, caption: string) => JSON.stringify([article.title, article.content, article.content.trim() ? '' : caption]);
export const captionsFingerprint = (value: ReadMoreDraft, type: string, media: string, caption: string, profiles: string[]) =>
  JSON.stringify([captionSourceOf(value.article, caption), type, media, [...profiles].sort()]);
export function captionsReady(value: ReadMoreDraft, type: string, media: string, caption: string, profiles: string[]) {
  return Boolean(value.captionsPreparationId && value.captionsFor === captionsFingerprint(value, type, media, caption, profiles)
    && profiles.every(id => value.captions?.[id]?.trim()));
}
/** Captions already written for this article, so preparing again only asks the AI about accounts that have none. */
export function knownCaptions(value: ReadMoreDraft, caption: string, profiles: string[]): Record<string, string> {
  if (!value.uniqueCaptions || value.captionSource !== captionSourceOf(value.article, caption)) return {};
  return Object.fromEntries(profiles.flatMap(id => value.captions?.[id] ? [[id, value.captions[id]]] : []));
}
export function readMoreReady(value: ReadMoreDraft | undefined, type: string, media: string, caption: string, profiles: string[]) {
  if (!value) return true;
  if (value.enabled && value.uniqueCaptions && !captionsReady(value, type, media, caption, profiles)) return false;
  if (value.enabled) return Boolean(value.preparation?.ready && value.preparedFingerprint === readMoreFingerprint(value, type, media, caption, profiles));
  return !value.uniqueCaptions || captionsReady(value, type, media, caption, profiles);
}
/** Drafts saved when unique captions were on by default did not choose them; ones already prepared keep them. */
export function savedReadMore(value?: ReadMoreDraft) {
  return value?.uniqueCaptions && !value.captionsPreparationId && !value.preparation ? { ...value, uniqueCaptions: false } : value;
}
/** What the queue receives: the website preparation, or captions prepared on their own. */
export function readMoreSelection(value?: ReadMoreDraft) {
  const id = value?.enabled ? value.preparation?.id : value?.uniqueCaptions ? value.captionsPreparationId : undefined;
  return id ? { read_more: { preparation_id: id, captions: value!.captions } } : {};
}
export async function websiteRequest<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const response = await fetch(`${API_BASE}/websites${path}`, body === undefined ? { method: 'GET' } : {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : Array.isArray(result.detail) ? result.detail.map((item: { msg: string }) => item.msg).join('; ') : 'Website request failed.');
  return result;
}

export const WEBSITE_CONNECTIONS_CHANGED = 'website-connections-changed';
export const MANAGE_WEBSITE_CONNECTIONS = 'manage-website-connections';
export const notifyWebsiteConnectionsChanged = () => window.dispatchEvent(new Event(WEBSITE_CONNECTIONS_CHANGED));
export const openWebsiteManagement = () => window.dispatchEvent(new Event(MANAGE_WEBSITE_CONNECTIONS));

export function parseWebsiteConnectionJson(text: string): {
  configuration: Record<string, unknown>; email: string; password: string; apiToken: string;
} {
  const parsed: unknown = JSON.parse(text);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Choose a website connection JSON object.');
  const value = parsed as Record<string, unknown>;
  if (typeof value.name !== 'string' || !value.name.trim() || typeof value.baseUrl !== 'string' || !value.baseUrl.trim()) {
    throw new Error('The website JSON needs a name and baseUrl.');
  }
  for (const key of ['email', 'username', 'password', 'api_token']) {
    if (value[key] !== undefined && typeof value[key] !== 'string') throw new Error('Website credentials must be text.');
  }
  const { password, email, username, api_token, ...configuration } = value;
  return { configuration, email: (email || username || '') as string,
    password: (password || '') as string, apiToken: (api_token || '') as string };
}
