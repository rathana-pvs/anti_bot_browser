import { useEffect, useRef, useState } from 'react';
import { Globe, Upload } from 'lucide-react';
import { Alert } from './ui/Alert';
import { Button } from './ui/Button';
import { EmptyState } from './ui/EmptyState';
import { Input } from './ui/Input';
import { notifyWebsiteConnectionsChanged, parseWebsiteConnectionJson, websiteRequest } from '../services/websites';
import type { WebsiteConnection } from '../services/websites';

export function WebsiteConnectionsSettings() {
  const [connections, setConnections] = useState<WebsiteConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [configuration, setConfiguration] = useState<Record<string, unknown> | null>(null);
  const [email, setEmail] = useState(''), [password, setPassword] = useState(''), [apiToken, setApiToken] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    websiteRequest<WebsiteConnection[]>('').then(value => { if (mounted.current) setConnections(value); })
      .catch(error => { if (mounted.current) setError(error.message); })
      .finally(() => { if (mounted.current) setLoading(false); });
    return () => { mounted.current = false; };
  }, []);
  const tokenAuth = (configuration?.schema as WebsiteConnection['schema'])?.authentication.type === 'bearer';
  const clearForm = () => { setConfiguration(null); setEmail(''); setPassword(''); setApiToken(''); };
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (error) { if (mounted.current) setError(error instanceof Error ? error.message : 'Website request failed.'); }
    finally { if (mounted.current) setBusy(false); }
  };
  const save = () => run(async () => {
    const saved = await websiteRequest<WebsiteConnection>('', { ...configuration, email, password, api_token: apiToken });
    notifyWebsiteConnectionsChanged();
    if (mounted.current) {
      setConnections(current => [...current.filter(item => item.id !== saved.id), saved]);
      clearForm(); setNotice(`${saved.name} saved. Select it when preparing a Read more post.`);
    }
  });
  const test = (connection: WebsiteConnection) => run(async () => {
    await websiteRequest(`/${connection.id}/test`, {});
    if (mounted.current) setNotice(`${connection.name}: connection successful.`);
  });
  const remove = (connection: WebsiteConnection) => run(async () => {
    await websiteRequest(`/${connection.id}`, {}, 'DELETE');
    notifyWebsiteConnectionsChanged();
    if (mounted.current) {
      setConnections(current => current.filter(item => item.id !== connection.id));
      setNotice(`${connection.name} removed. Scheduled posts keep their saved captions and links.`);
    }
  });
  const updateLogin = (connection: WebsiteConnection) => {
    clearForm(); setError(''); setNotice('');
    setConfiguration({ schemaVersion: connection.schema ? 2 : 1, name: connection.name, baseUrl: connection.baseUrl,
      adapter: connection.adapter, ...(connection.schema ? { schema: connection.schema } : {}) });
  };
  return <div className="max-w-xl space-y-4">
    <p className="text-xs leading-5 text-muted">Upload a website JSON and save its login once. Choose the saved website when posting a Photo, Reel, or Live with Read more.</p>
    <input ref={input} type="file" accept=".json,application/json" aria-label="Website connection JSON" className="hidden" disabled={busy || loading} onChange={event => {
      const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
      if (file) void run(async () => {
        if (file.size > 64000) throw new Error('Website JSON must be under 64 KB.');
        const imported = parseWebsiteConnectionJson(await file.text());
        if (mounted.current) {
          setConfiguration(imported.configuration); setEmail(imported.email); setPassword(imported.password); setApiToken(imported.apiToken);
        }
      });
    }} />
    <div className="flex flex-wrap items-center gap-3">
      <Button type="button" variant="outline" disabled={busy || loading} onClick={() => input.current?.click()}><Upload className="h-4 w-4" />Upload website JSON</Button>
      <a className="text-xs text-blue-300 underline" href="/website-connection.example.json" download>Download simple example</a>
    </div>
    {error && <Alert variant="danger">{error}</Alert>}
    {notice && <Alert variant="success">{notice}</Alert>}
    {configuration && <form className="space-y-3 rounded-xl border border-border bg-canvas p-4" onSubmit={event => { event.preventDefault(); void save(); }}>
      <p className="text-sm font-medium text-foreground">{String(configuration.name)}</p>
      <p className="break-all text-xs text-muted">{String(configuration.baseUrl)}</p>
      <fieldset disabled={busy} className="space-y-3">
        {tokenAuth ? <label className="block space-y-1 text-xs text-muted">API token<Input type="password" autoComplete="off" required value={apiToken} onChange={event => setApiToken(event.target.value)} /></label> : <>
          <label className="block space-y-1 text-xs text-muted">Email or username<Input autoComplete="username" required value={email} onChange={event => setEmail(event.target.value)} /></label>
          <label className="block space-y-1 text-xs text-muted">Password<Input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /></label>
        </>}
        <div className="flex flex-wrap gap-2"><Button type="submit" loading={busy}>Test connection & save</Button><Button type="button" variant="ghost" onClick={clearForm}>Cancel</Button></div>
      </fieldset>
    </form>}
    {loading ? <p role="status" className="text-xs text-muted">Loading saved websites…</p> : connections.length === 0 ?
      <EmptyState icon={<Globe className="h-6 w-6" />} title="No websites saved" description="Upload your website JSON above to connect it." /> :
      <div className="space-y-3">{connections.map(connection => <div key={connection.id} className="space-y-3 rounded-xl border border-border bg-canvas p-4">
        <div><p className="text-sm font-medium text-foreground">{connection.name}</p><p className="mt-1 break-all text-xs text-muted">{connection.baseUrl}</p></div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => test(connection)}>Test connection</Button>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => updateLogin(connection)}>Update login</Button>
          <Button type="button" size="sm" variant="ghost" className="text-red-400" disabled={busy} onClick={() => remove(connection)}>Remove</Button>
        </div>
      </div>)}</div>}
  </div>;
}
