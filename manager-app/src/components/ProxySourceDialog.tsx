import React, { useState } from 'react';
import { Loader2, Trash2 } from 'lucide-react';
import { createProxySource, deleteProxySource, ProxySourceInput, updateProxySource } from '../services/api';
import { PROXY_USE_LABEL, ProxyProtocol, ProxyProvider, ProxySource, ProxyUse } from '../types/proxy';
import { Dialog } from './ui/Dialog';
import { Select } from './ui/Select';
import { useAppDialog } from './ui/AppDialogProvider';

const DEFAULT_LIMIT = 'default';
const LIMIT_OPTIONS = Array.from({ length: 20 }, (_, i) => i + 1);
const inputClass = 'w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-200 outline-none focus:border-blue-500';

/** Adds a proxy source, or edits one (`source`). Keys are checked with the provider before they are saved. */
export const ProxySourceDialog: React.FC<{
  source: ProxySource | null;
  globalLimit: number;
  onClose: () => void;
  onSaved: (source: ProxySource | null) => void;
}> = ({ source, globalLimit, onClose, onSaved }) => {
  const { showConfirm } = useAppDialog();
  const [name, setName] = useState(source?.name ?? '');
  const [provider, setProvider] = useState<ProxyProvider>(source?.provider ?? 'webshare');
  const [protocol, setProtocol] = useState<ProxyProtocol>(source?.protocol ?? 'socks5');
  const [limit, setLimit] = useState(source?.accounts_per_proxy ? String(source.accounts_per_proxy) : DEFAULT_LIMIT);
  const [useOn, setUseOn] = useState<ProxyUse>(source?.use_on ?? 'both');
  const [key, setKey] = useState('');
  const [removeKey, setRemoveKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasProxies = (source?.proxy_count ?? 0) > 0;
  const needsKey = provider === 'webshare';

  const changeProtocol = (value: string) => {
    setProtocol(value as ProxyProtocol);
    // Phones only use SOCKS5.
    if (value === 'http') setUseOn('pc');
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving) return;
    if (!name.trim()) { setError('Enter a name for the source.'); return; }
    setSaving(true);
    setError(null);
    const input: ProxySourceInput = {
      name: name.trim(), provider, use_on: useOn,
      accounts_per_proxy: limit === DEFAULT_LIMIT ? null : Number(limit),
      ...(!hasProxies ? { protocol } : {}),
      ...(needsKey && key.trim() ? { api_key: key.trim() } : needsKey && removeKey ? { api_key: '' } : {}),
    };
    try {
      onSaved(source ? await updateProxySource(source.id, input) : await createProxySource(input));
      onClose();
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!source) return;
    const confirmed = await showConfirm(`Delete the source “${source.name}”? Its saved API key is removed from this PC.`, {
      title: 'Delete proxy source', confirmLabel: 'Delete source', variant: 'danger',
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await deleteProxySource(source.id);
      onSaved(null);
      onClose();
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  };

  return <Dialog open onOpenChange={open => { if (!open && !saving) onClose(); }}
    title={source ? `Source settings · ${source.name}` : 'Add proxy source'}
    description="A source is one provider account or one batch of proxies. Each proxy belongs to one source.">
    <form onSubmit={save} className="space-y-3 text-xs">
      <label className="block space-y-1.5">
        <span className="text-zinc-400">Name</span>
        <input autoFocus aria-label="Source name" value={name} maxLength={60} onChange={e => setName(e.target.value)} placeholder="Webshare residential" className={inputClass} />
      </label>
      <div className="space-y-1.5">
        <span className="text-zinc-400">Provider</span>
        <Select value={provider} onValueChange={value => setProvider(value as ProxyProvider)} ariaLabel="Provider" disabled={saving} contentClassName="z-[400]"
          options={[{ value: 'webshare', label: 'Webshare' }, { value: 'manual', label: 'Manual (no API)' }]} />
      </div>
      {needsKey && <div className="space-y-1.5">
        <span className="text-zinc-400">Webshare API key</span>
        {source?.key_set && !removeKey && <p className="text-[11px] text-zinc-500">A key is saved{source.key_hint ? ` (${source.key_hint})` : ''}. Enter a new one to replace it.</p>}
        <input type="password" autoComplete="off" value={key} onChange={e => { setKey(e.target.value); setRemoveKey(false); }}
          placeholder={source?.key_set ? 'Keep the saved key' : 'Paste the API key'} className={`${inputClass} font-mono`} aria-label="Webshare API key" />
        <p className="text-[11px] text-zinc-500">Saved only on this PC and sent only to Webshare. Each source keeps its own key.</p>
        {source?.key_set && <button type="button" disabled={saving} onClick={() => { setRemoveKey(!removeKey); setKey(''); }}
          className="text-[11px] text-red-300 hover:text-red-200">{removeKey ? 'Keep the saved key' : 'Remove the saved key'}</button>}
      </div>}
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1.5">
          <span className="text-zinc-400">Protocol</span>
          <Select value={protocol} onValueChange={changeProtocol} ariaLabel="Protocol" disabled={saving || hasProxies} contentClassName="z-[400]"
            options={[{ value: 'socks5', label: 'SOCKS5' }, { value: 'http', label: 'HTTP' }]} />
        </div>
        <div className="space-y-1.5">
          <span className="text-zinc-400">Accounts per proxy</span>
          <Select value={limit} onValueChange={setLimit} ariaLabel="Accounts per proxy" disabled={saving} contentClassName="z-[400]"
            options={[{ value: DEFAULT_LIMIT, label: `Default (${globalLimit})` }, ...LIMIT_OPTIONS.map(n => ({ value: String(n), label: String(n) }))]} />
        </div>
      </div>
      {hasProxies && <p className="text-[11px] text-zinc-500">The protocol is fixed once a source has proxies.</p>}
      <div className="space-y-1.5">
        <span className="text-zinc-400">Use on</span>
        <Select value={useOn} onValueChange={value => setUseOn(value as ProxyUse)} ariaLabel="Use on" disabled={saving || protocol === 'http'} contentClassName="z-[400]"
          options={(['both', 'pc', 'phone'] as ProxyUse[]).map(value => ({ value, label: PROXY_USE_LABEL[value] }))} />
        {protocol === 'http' && <p className="text-[11px] text-zinc-500">Phones only use SOCKS5, so HTTP proxies are for PC accounts.</p>}
      </div>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <div className="flex items-center justify-end gap-2 border-t border-zinc-800 pt-3">
        {source && !source.is_default && <button type="button" onClick={() => void remove()} disabled={saving || hasProxies}
          title={hasProxies ? 'Delete or move its proxies first' : undefined}
          className="mr-auto flex items-center gap-1.5 px-2 py-1.5 text-red-300 hover:text-red-200 disabled:opacity-40">
          <Trash2 className="h-3.5 w-3.5" />Delete source</button>}
        <button type="button" disabled={saving} onClick={onClose}
          className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300 hover:bg-zinc-800">Cancel</button>
        <button type="submit" disabled={saving}
          className="flex items-center gap-1.5 rounded-md bg-white px-4 py-1.5 font-semibold text-zinc-950 hover:bg-zinc-200 disabled:opacity-50">
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {saving ? (needsKey && key.trim() ? 'Checking key…' : 'Saving…') : needsKey && key.trim() ? 'Check key and save' : 'Save'}
        </button>
      </div>
    </form>
  </Dialog>;
};
