import React, { useEffect, useState } from 'react';
import { accountLimit, formatProxyGeography, hasRoom, PROXY_FOLDERS, PROXY_USE_LABEL, ProxyItem, ProxySource, proxyHolders, proxyIpListText, qualityRank } from '../types/proxy';
import { checkProxyGeography, fetchProxySettings, fetchProxySources, importProxies, moveProxiesIntoSource, syncProxySource, refreshProxyStatuses, testProxyPing, deleteSelectedProxies, saveProxyList, updateAccountsPerProxy } from '../services/api';
import type { ProxyElsewhere } from '../services/api';
import { Download, Globe, Plus, Trash2, Activity, MapPin, X, Loader2, Search, Lock, Folder, Gauge, Monitor, Smartphone, Users, RefreshCw, Settings2 } from 'lucide-react';
import { useAppDialog } from './ui/AppDialogProvider';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';
import { SortButton, sortRows, useSort } from './ui/sort';
import { useProxyLocationFilter } from './ProxyLocationFilter';
import { CheckRunBar, ProxyQualityCell, useProxyCheckRun } from './ProxyCheck';
import { useWebshare, WebshareRunBar } from './WebshareReplace';
import { ALL_FOLDERS } from './accounts/FolderList';
import { ALL_SOURCES, ProxySourceList } from './ProxySourceList';
import { ProxySourceDialog } from './ProxySourceDialog';
import { isAndroid } from './accounts/accountModel';
import { Profile } from '../types/profile';

const ACCOUNT_LIMIT_OPTIONS = Array.from({ length: 20 }, (_, i) => i + 1);

type ProxySortKey = 'proxy' | 'location' | 'assignment' | 'latency' | 'quality';

interface ProxyPanelProps {
  proxies: ProxyItem[];
  profiles: Profile[];
  onRefresh: () => void;
  onSelectProfile?: (profileId: string) => void;
}

export const ProxyPanel: React.FC<ProxyPanelProps> = ({
  proxies,
  profiles,
  onRefresh,
  onSelectProfile,
}) => {
  const { showAlert, showConfirm } = useAppDialog();
  const [folder, setFolder] = useState(ALL_FOLDERS);
  const [sourceId, setSourceId] = useState(ALL_SOURCES);
  const [sources, setSources] = useState<ProxySource[]>([]);
  // null: closed · 'new': adding one · otherwise the source being edited.
  const [editing, setEditing] = useState<ProxySource | 'new' | null>(null);
  const loadSources = () => fetchProxySources().then(setSources).catch(() => {});
  useEffect(() => { void loadSources(); }, [proxies]);
  const source = sources.find(s => s.id === sourceId) ?? null;
  const sourceById = new Map(sources.map(s => [s.id, s]));
  // A source that was deleted, or never loaded, falls back to all proxies.
  useEffect(() => { if (sourceId !== ALL_SOURCES && sources.length && !source) setSourceId(ALL_SOURCES); }, [sources, sourceId, source]);
  const inSource = (proxy: ProxyItem) => sourceId === ALL_SOURCES || (proxy.source_id || 'default') === sourceId;
  const sourceProxies = proxies.filter(inSource);
  const [importSource, setImportSource] = useState('default');
  const checkRun = useProxyCheckRun(onRefresh);
  const checkingIds = new Set(checkRun.run.running_ids || []);
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [isImporting, setIsImporting] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [geoCheckingId, setGeoCheckingId] = useState<string | null>(null);
  const [isRefreshingStatus, setIsRefreshingStatus] = useState(false);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const location = useProxyLocationFilter(proxies);
  const inLocation = new Set(location.filtered.map(p => p.id));
  const [isDeleting, setIsDeleting] = useState(false);
  const webshare = useWebshare(onRefresh);
  const busy = isDeleting || isImporting || isSyncing || isRefreshingStatus || checkRun.running || webshare.running;
  // A source that uses its provider's API only gets proxies by syncing; pasted ones go to a manual source.
  const importable = sources.filter(s => !s.uses_api);
  const openImport = () => {
    setImportSource(source && !source.uses_api ? source.id : importable[0]?.id ?? '');
    setIsImportOpen(true);
  };
  const inUse = (proxy: ProxyItem) => proxyHolders(proxy).length > 0;
  const accountsOf = (proxy: ProxyItem) => proxyHolders(proxy).map(id => ({ id, profile: profiles.find(profile => profile.id === id) }));
  const { sort, toggle } = useSort<ProxySortKey>();
  const proxyValue = (proxy: ProxyItem, key: ProxySortKey): string | number | null => {
    switch (key) {
      case 'proxy': return `${proxy.host}:${proxy.port}`;
      case 'location': return [proxy.country_code || proxy.country, proxy.city].filter(Boolean).join(' ') || null;
      case 'assignment': return proxyHolders(proxy).length;
      case 'latency': return proxy.latency_ms;
      case 'quality': return qualityRank(proxy);
    }
  };
  // The global setting; a source may set its own limit instead.
  const [globalLimit, setGlobalLimit] = useState(1);
  useEffect(() => { fetchProxySettings().then(s => setGlobalLimit(s.accounts_per_proxy)).catch(() => {}); }, []);
  const limit = source?.accounts_per_proxy ?? globalLimit;
  const anyShared = sourceProxies.some(p => accountLimit(p) > 1);
  const [savingLimit, setSavingLimit] = useState(false);
  const assigned = sourceProxies.filter(inUse).length;
  const withRoom = sourceProxies.filter(p => p.group !== 'Poor proxy' && hasRoom(p)).length;
  const available = sourceProxies.filter(p => p.group === 'Available').length;
  const poor = sourceProxies.filter(p => p.group === 'Poor proxy').length;
  const query = search.trim().toLowerCase();
  const filteredProxies = sourceProxies.filter((proxy) => {
    if (folder !== ALL_FOLDERS && proxy.group !== folder) return false;
    if (filter === 'available' && inUse(proxy)) return false;
    if (filter === 'assigned' && !inUse(proxy)) return false;
    if (!inLocation.has(proxy.id)) return false;
    return !query || [proxy.host, String(proxy.port), proxy.username, ...accountsOf(proxy).flatMap(a => [a.id, a.profile?.name]),
      proxy.exit_ip, proxy.group, proxy.source_name, formatProxyGeography(proxy)].filter(Boolean).join(' ').toLowerCase().includes(query);
  });
  // A clicked column sorts within each folder; otherwise assigned proxies come first.
  const visibleProxies = sort
    ? sortRows(filteredProxies, sort, proxyValue)
    : [...filteredProxies].sort((a, b) => Number(inUse(b)) - Number(inUse(a)));
  const selectableIds = visibleProxies.map((proxy) => proxy.id);
  const groups = PROXY_FOLDERS.filter(group => visibleProxies.some(p => p.group === group));
  const selectedVisible = selectableIds.filter((id) => selectedIds.has(id)).length;
  // Only proxies from a Webshare source with a saved key can get a new IP from here.
  const replaceable = proxies.filter(p => selectedIds.has(p.id) && !p.replaced_by && (() => {
    const from = sourceById.get(p.source_id || 'default');
    return from?.provider === 'webshare' && from.key_set;
  })());
  // Replace and move also takes a proxy Webshare already replaced whose accounts are still on its old IP.
  const movable = proxies.filter(p => selectedIds.has(p.id) && proxyHolders(p).length > 0
    && (!p.replaced_by || proxies.some(other => other.id === p.replaced_by)) && (() => {
      const from = sourceById.get(p.source_id || 'default');
      return from?.provider === 'webshare' && from.key_set;
    })());
  const allVisibleSelected = selectableIds.length > 0 && selectedVisible === selectableIds.length;

  useEffect(() => {
    const availableIds = new Set(proxies.map((proxy) => proxy.id));
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => availableIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [proxies]);

  const toggleSelection = (ids: string[], checked: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => checked ? next.add(id) : next.delete(id));
      return next;
    });
  };

  /** Checks these proxies several at once. A single proxy, or `force`, is checked even if it was checked today. */
  const checkProxies = async (ids?: string[], force = false) => {
    try {
      await checkRun.start(ids, force || ids?.length === 1);
    } catch (err: any) {
      await showAlert(err.message || String(err), { title: 'Could not start the proxy check', variant: 'danger' });
    }
  };

  const handleSync = async () => {
    if (!source) return;
    setIsSyncing(true);
    try {
      const result = await syncProxySource(source.id);
      const elsewhere = result.in_other_sources.length + result.kept_elsewhere.length;
      const summary = `Webshare lists ${result.listed} proxies. Added ${result.added} new ones to ${source.name}`
        + (result.skipped - elsewhere > 0 ? `; ${result.skipped - elsewhere} were already here.` : '.')
        + (result.added ? ` Online: ${result.online}; offline: ${result.offline}; locations found: ${result.geo_checked}.` : '')
        + (result.not_in_webshare ? ` ${result.not_in_webshare} proxies in this source are no longer in your Webshare account; they were kept so their accounts don't lose them.` : '')
        + keptNote(result.kept_elsewhere);
      onRefresh();
      const movable = result.in_other_sources;
      if (!movable.length) {
        await showAlert(summary, { title: 'Webshare sync complete' });
        return;
      }
      const used = movable.filter(item => item.accounts > 0).length;
      const move = await showConfirm(`${summary}\n\n${countBySource(movable)}. `
        + `Move ${movable.length === 1 ? 'it' : 'them'} to ${source.name}?`
        + (used ? ` ${used} ${used === 1 ? 'is' : 'are'} used by accounts.` : '')
        + ' Only the source changes: accounts keep their proxies, and nothing is replaced.',
        { title: 'Webshare sync complete', confirmLabel: `Move ${movable.length} to ${source.name}`, cancelLabel: 'Leave them' });
      if (!move) return;
      const moved = await moveProxiesIntoSource(source.id, movable.map(item => item.id));
      await showAlert(`Moved ${moved.moved} ${moved.moved === 1 ? 'proxy' : 'proxies'} to ${source.name}.` + keptNote(moved.kept)
        + (moved.not_movable ? ` ${moved.not_movable} had already changed and stayed where they were.` : ''), { title: 'Proxies moved' });
      onRefresh();
    } catch (err: any) {
      await showAlert(`Sync failed: ${err.message}`, { title: 'Webshare sync failed', variant: 'danger' });
    } finally {
      setIsSyncing(false);
    }
  };

  const handleImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importText.trim() || !importSource) return;

    setIsImporting(true);
    try {
      const result = await importProxies(importText, importSource);
      const into = sourceById.get(importSource)?.name;
      await showAlert(`Added ${result.added} proxies${into ? ` to ${into}` : ''} (${result.total} total).`
        + (result.skipped ? ` ${result.skipped} already in the pool were skipped.` : '')
        + ` Online: ${result.online}; offline: ${result.offline}; locations found: ${result.geo_checked}.`, { title: 'Proxy import complete' });
      setImportText('');
      setIsImportOpen(false);
      onRefresh();
    } catch (err: any) {
      await showAlert(`Import failed: ${err.message}`, { title: 'Proxy import failed', variant: 'danger' });
    } finally {
      setIsImporting(false);
    }
  };

  const handleGeoCheck = async (proxyId: string) => {
    setGeoCheckingId(proxyId);
    try {
      await checkProxyGeography(proxyId);
      onRefresh();
    } catch (err: any) {
      await showAlert(`Location check failed: ${err.message}`, { title: 'Location check failed', variant: 'danger' });
    } finally {
      setGeoCheckingId(null);
    }
  };

  const handleRefreshStatus = async () => {
    setIsRefreshingStatus(true);
    try {
      const result = await refreshProxyStatuses();
      onRefresh();
      await showAlert(`Checked ${result.total} proxies. Online: ${result.online}; offline: ${result.offline}; locations found: ${result.geo_checked}.`, { title: 'Proxy status updated' });
    } catch (err: any) {
      await showAlert(`Proxy status refresh failed: ${err.message}`, { title: 'Proxy refresh failed', variant: 'danger' });
    } finally {
      setIsRefreshingStatus(false);
    }
  };

  const handleTestPing = async (proxyId: string) => {
    setTestingId(proxyId);
    try {
      await testProxyPing(proxyId);
      onRefresh();
    } catch (err: any) {
      await showAlert(`Ping test failed: ${err.message}`, { title: 'Proxy test failed', variant: 'danger' });
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async (ids: string[]) => {
    if (busy || ids.length === 0) return;
    setIsDeleting(true);
    try {
      const confirmed = await showConfirm(
        `Remove ${ids.length === 1 ? 'this proxy' : `these ${ids.length} proxies`} from the pool?`, {
          title: ids.length === 1 ? 'Delete proxy' : 'Delete selected proxies',
          confirmLabel: ids.length === 1 ? 'Delete proxy' : `Delete ${ids.length} proxies`, variant: 'danger',
        },
      );
      if (!confirmed) return;
      const result = await deleteSelectedProxies(ids);
      // The delete is done, so the selection is too, including any assigned proxies that were kept.
      setSelectedIds(new Set());
      onRefresh();
      if (result.blocked_ids.length) {
        await showAlert(`Deleted ${result.deleted_ids.length} proxies. ${result.blocked_ids.length} assigned proxies were kept.`, {
          title: 'Some proxies were kept', variant: 'warning',
        });
      } else if (result.deleted_ids.length) {
        await showAlert(`Deleted ${result.deleted_ids.length} ${result.deleted_ids.length === 1 ? 'proxy' : 'proxies'}.`, { variant: 'success' });
      }
    } catch (err: any) {
      await showAlert(err.message || 'Could not delete the selected proxies.', { title: 'Proxy deletion failed', variant: 'danger' });
    } finally {
      setIsDeleting(false);
    }
  };

  const changeLimit = async (value: string) => {
    const next = Number(value);
    if (next === globalLimit) return;
    setSavingLimit(true);
    try {
      setGlobalLimit((await updateAccountsPerProxy(next)).accounts_per_proxy);
      onRefresh();
    } catch (err: any) {
      await showAlert(err.message || String(err), { title: 'Could not change accounts per proxy', variant: 'danger' });
    } finally {
      setSavingLimit(false);
    }
  };

  const handleExport = async () => {
    const selected = proxies.filter(p => selectedIds.has(p.id));
    if (!selected.length) return;
    try {
      const savedTo = await saveProxyList(proxyIpListText(selected));
      const ips = new Set(selected.map(p => p.host)).size;
      await showAlert(`Saved ${ips} ${ips === 1 ? 'IP' : 'IPs'} to ${savedTo}.`, { title: 'Proxy IPs exported', variant: 'success' });
    } catch (err: any) {
      await showAlert(err.message || String(err), { title: 'Proxy export failed', variant: 'danger' });
    }
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden bg-background select-none">
      <ProxySourceList sources={sources} proxies={proxies} sourceId={sourceId} folder={folder}
        onSelect={(nextSource, nextFolder) => { setSourceId(nextSource); setFolder(nextFolder); }}
        onAdd={() => setEditing('new')} onEdit={setEditing} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-hidden p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-base font-semibold text-zinc-100"><Globe className="h-4 w-4 text-zinc-400" />
              {source ? source.name : 'Proxies'}{folder !== ALL_FOLDERS && <span className="font-normal text-zinc-500">· {folder}</span>}</h1>
            <p className="mt-1 text-xs text-zinc-500">{source
              ? [source.provider_label, source.protocol.toUpperCase(),
                source.provider === 'webshare' && (source.key_set ? `key ${source.key_hint ?? 'saved'}` : 'no API key'),
                `${limit} ${limit === 1 ? 'account' : 'accounts'} per proxy${source.accounts_per_proxy ? '' : ' (default)'}`,
                PROXY_USE_LABEL[source.use_on]].filter(Boolean).join(' · ')
              : 'Proxies your accounts connect through, from every source.'}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {source ? <button onClick={() => setEditing(source)} disabled={busy}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
            <Settings2 className="h-3.5 w-3.5" />Source settings
          </button> : <label className="flex items-center gap-2 text-xs text-zinc-400" title="How many accounts can share one proxy, for sources without their own limit. Accounts already on a proxy keep it when you lower this.">
            <Users className="h-3.5 w-3.5" />Default accounts per proxy
            <div className="w-20">
              <Select value={String(globalLimit)} onValueChange={changeLimit} ariaLabel="Default accounts per proxy" disabled={savingLimit || busy} className="min-h-8 py-1.5"
                options={ACCOUNT_LIMIT_OPTIONS.map(n => ({ value: String(n), label: String(n) }))} />
            </div>
          </label>}
          <button onClick={() => void checkProxies(source ? sourceProxies.map(p => p.id) : undefined)} disabled={busy || !sourceProxies.length}
            title="Connection, Facebook, exit IP and speed, several proxies at once. Skips proxies that worked in the last 24 hours; Poor ones are always checked again."
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
            <Gauge className="h-3.5 w-3.5" />Check proxies
          </button>
          <button onClick={handleRefreshStatus} disabled={busy || proxies.length === 0}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
            {isRefreshingStatus ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Activity className="h-3.5 w-3.5" />}
            {isRefreshingStatus ? 'Checking…' : 'Check status'}
          </button>
          {source?.uses_api ? <button onClick={() => void handleSync()} disabled={busy}
            title={`Adds the proxies in this Webshare account that aren't in the pool yet. Proxies of ${source.name} come only from its API.`}
            className="flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-zinc-200 disabled:opacity-40">
            {isSyncing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            {isSyncing ? 'Syncing…' : 'Sync from Webshare'}
          </button> : <button onClick={openImport} disabled={busy || !importable.length}
            title={importable.length ? undefined : 'Every source gets its proxies from an API. Add a manual source to paste proxies.'}
            className="flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-zinc-200 disabled:opacity-40">
            <Plus className="h-3.5 w-3.5" />Import proxies
          </button>}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-zinc-500">
        <span><strong className="font-medium text-zinc-200">{sourceProxies.length}</strong> total</span>
        <span><strong className="font-medium text-emerald-400">{available}</strong> available</span>
        <span><strong className="font-medium text-zinc-300">{assigned}</strong> used</span>
        {anyShared && <span><strong className="font-medium text-sky-300">{withRoom}</strong> with room for another account</span>}
        <span><strong className="font-medium text-amber-400">{poor}</strong> poor</span>
      </div>

      <div className="text-[11px] text-zinc-500" aria-live="polite">
        {source || !sources.some(s => s.accounts_per_proxy)
          ? `${limit === 1 ? 'Each proxy serves one account.' : `Each proxy serves up to ${limit} accounts.`} `
          : 'Each source sets how many accounts share one proxy. '}
        {'Used proxies stay locked to their accounts. '}
        Proxies move to Poor proxy only when download or upload times out, or they can't connect. Speeds under 1 Mbps show in yellow but stay in Available.
      </div>
      <CheckRunBar run={checkRun.run} showSummary={checkRun.showSummary} onStop={() => void checkRun.stop()} onDismiss={checkRun.dismiss}
        onCheckSkipped={() => void checkProxies(undefined, true)} />
      <WebshareRunBar run={webshare.run} />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="relative min-w-40 flex-1">
            <Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
            <input value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Search proxies"
              placeholder="Search host, folder, location, or profile…"
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 py-2 pl-8 pr-3 text-xs text-zinc-200 outline-none focus:border-blue-500" />
          </div>
          <div className="w-36">
            <Select value={filter} onValueChange={setFilter} ariaLabel="Filter proxies" className="min-h-8 py-2"
              options={[{ value: 'all', label: 'All proxies' }, { value: 'available', label: 'Available' }, { value: 'assigned', label: 'Assigned' }]} />
          </div>
          {location.controls({ className: "w-48", selectClassName: "min-h-8 py-2" })}
          {selectedIds.size > 0 && (
            <>
              <button onClick={() => void checkProxies([...selectedIds], true)} disabled={busy} className="px-2 py-1.5 text-xs text-blue-300 disabled:opacity-40">Check selected</button>
              <button onClick={handleExport}
                className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800">
                <Download className="h-3.5 w-3.5" />Export IPs ({selectedIds.size})
              </button>
              {replaceable.length > 0 && <button disabled={busy}
                onClick={() => void webshare.replace(replaceable.map(p => p.id), new Set(replaceable.flatMap(proxyHolders)).size)}
                title={`Replace: Webshare gives each selected proxy a new IP in the same country, using its own source's key. Accounts stay put until you change their proxy.${replaceable.length < selectedIds.size ? ` ${selectedIds.size - replaceable.length} selected can't be replaced (not from a Webshare source with a key, or already replaced).` : ''}`}
                className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
                <RefreshCw className="h-3.5 w-3.5" />Replace IP · Webshare ({replaceable.length})
              </button>}
              {movable.length > 0 && <button disabled={busy}
                onClick={() => void webshare.replaceAndMove(movable.map(p => p.id), new Set(movable.flatMap(proxyHolders)).size)}
                title="Replace and move: stops each proxy's accounts (browser closed; phone on its main user, the account's user stopped), replaces the IP at Webshare, then moves the accounts to the new IP. A proxy with a job running on any of its accounts is left alone."
                className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
                <RefreshCw className="h-3.5 w-3.5" />Replace and move accounts ({movable.length})
              </button>}
              <button onClick={() => setSelectedIds(new Set())} disabled={busy} className="px-2 py-1.5 text-xs text-zinc-400 hover:text-white disabled:opacity-40">Clear selection</button>
              <button onClick={() => handleDelete([...selectedIds])} disabled={busy || !proxies.some(p => selectedIds.has(p.id) && !inUse(p))}
                className="flex items-center gap-1.5 rounded-md border border-red-900/60 bg-red-950/30 px-3 py-1.5 text-xs text-red-300 hover:bg-red-950/60 disabled:opacity-40">
                {isDeleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                {isDeleting ? 'Deleting…' : `Delete selected (${selectedIds.size})`}
              </button>
            </>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="sticky top-0 z-10 bg-zinc-950 text-[10px] uppercase tracking-wide text-zinc-500">
              <tr className="border-b border-border">
                <th className="w-10 py-2.5 pl-3 font-medium">
                  <Checkbox ariaLabel="Select all proxies in view" disabled={busy || selectableIds.length === 0}
                    checked={allVisibleSelected ? true : selectedVisible > 0 ? 'indeterminate' : false}
                    onCheckedChange={(checked) => toggleSelection(selectableIds, checked)} />
                </th>
                <th className="px-3 py-2.5 font-medium"><SortButton label="Proxy" column="proxy" sort={sort} onToggle={toggle} /></th>
                <th className="px-3 py-2.5 font-medium"><SortButton label="Location / timezone" column="location" sort={sort} onToggle={toggle} /></th>
                <th className="px-3 py-2.5 font-medium"><SortButton label="Assignment" column="assignment" sort={sort} onToggle={toggle} /></th>
                <th className="px-3 py-2.5 font-medium"><SortButton label="Latency" column="latency" sort={sort} onToggle={toggle} /></th>
                <th className="px-3 py-2.5 font-medium"><SortButton label="Quality" column="quality" sort={sort} onToggle={toggle} /></th>
                <th className="w-28 px-3 py-2.5 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {groups.map(group => <React.Fragment key={group}>
                <tr className="bg-zinc-900/80"><td colSpan={7} className="px-3 py-2">
                  <div className="flex items-center gap-3">
                    <Folder className="h-3.5 w-3.5 text-blue-300" />
                    <span className="font-medium text-zinc-200">{group}</span>
                    <span className="text-zinc-500">{sourceProxies.filter(p => (p.group || '') === group).length} proxies</span>
                    <button disabled={busy} onClick={() => void checkProxies(sourceProxies.filter(p => (p.group || '') === group).map(p => p.id), true)}
                      className="ml-auto flex items-center gap-1.5 text-blue-300 disabled:opacity-40"><Gauge className="h-3.5 w-3.5" />Check folder</button>
                  </div>
                </td></tr>
              {visibleProxies.filter(p => (p.group || '') === group).map((proxy) => {
                const accounts = accountsOf(proxy);
                return (
                <tr key={proxy.id} className={selectedIds.has(proxy.id) ? 'bg-blue-500/5' : 'hover:bg-zinc-800/30'}>
                  <td className="py-2.5 pl-3">
                    <Checkbox ariaLabel={`Select ${proxy.host}:${proxy.port}`} checked={selectedIds.has(proxy.id)}
                      disabled={busy} onCheckedChange={(checked) => toggleSelection([proxy.id], checked)} />
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <span role="img" aria-label={proxy.reachable === true ? 'Online' : proxy.reachable === false ? 'Offline' : 'Status unknown'}
                        title={proxy.last_checked ? `Last checked ${new Date(proxy.last_checked).toLocaleString()}` : 'Not checked yet'}
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${proxy.reachable === true ? 'bg-emerald-400' : proxy.reachable === false ? 'bg-red-400' : 'bg-zinc-600'}`} />
                      <span className="select-text font-mono text-[11px] text-zinc-200">{proxy.host}:{proxy.port}</span>
                      {proxy.replaced_by && <span title={`Replaced at Webshare${proxy.replaced_at ? ` on ${new Date(proxy.replaced_at).toLocaleString()}` : ''}. It no longer works and takes no new account. Change the proxy of each account on it (right-click the account → Change proxy).`}
                        className="rounded-md border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-300">
                        Replaced{proxy.replaced_by_address ? ` → ${proxy.replaced_by_address}` : ''}</span>}
                      {proxy.replaced_from && !proxy.replaced_by && <span title={`New IP for ${proxy.replaced_from}`}
                        className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">New</span>}
                    </div>
                    <p className="mt-1 pl-3.5 text-[10px] text-zinc-500">{proxy.username || 'No authentication'} · {proxy.type.toUpperCase()}{!source && proxy.source_name ? ` · ${proxy.source_name}` : ''}</p>
                  </td>
                  <td className="max-w-64 px-3 py-2.5" title={proxy.geo_last_error || formatProxyGeography(proxy)}>
                    <p className="truncate text-[11px] text-zinc-300">{proxy.geo_source === 'unknown' ? 'Location unknown' : [proxy.city, proxy.country_code || proxy.country].filter(Boolean).join(', ') || 'Location unknown'}</p>
                    <p className="mt-1 truncate text-[10px] text-zinc-500">{proxy.geo_source !== 'unknown' && proxy.timezone ? proxy.timezone : 'Timezone not detected'}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    {(accountLimit(proxy) > 1 || accounts.length > 1) && <p className={`mb-1 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
                      accounts.length > accountLimit(proxy) ? 'bg-amber-500/10 text-amber-300' : accounts.length === accountLimit(proxy) ? 'bg-zinc-800 text-zinc-300' : 'bg-sky-500/10 text-sky-300'}`}
                      title={accounts.length > accountLimit(proxy) ? 'More accounts than the current limit. They keep this proxy, but no new account can join.' : undefined}>
                      <Users className="h-3 w-3" aria-label="Accounts" />{accounts.length}/{accountLimit(proxy)}</p>}
                    {inUse(proxy) ? (<div className="space-y-1">{accounts.map(({ id, profile: account }) => <div key={id} className="flex flex-wrap items-center gap-2">
                      <button disabled={!onSelectProfile} onClick={() => onSelectProfile?.(id)} title={id}
                        className="inline-flex items-center gap-1.5 text-[11px] text-blue-300 hover:text-blue-200 disabled:text-zinc-400">
                        <Lock className="h-3 w-3" />{account?.name || id}
                      </button>
                      {account && <span title={isAndroid(account) ? 'Phone' : 'PC'} aria-label={isAndroid(account) ? 'Phone' : 'PC'}
                        className={`inline-flex items-center rounded-md p-1 ${isAndroid(account) ? 'bg-teal-500/10 text-teal-300' : 'bg-violet-500/10 text-violet-300'}`}>
                        {isAndroid(account) ? <Smartphone className="h-3 w-3" aria-hidden="true" /> : <Monitor className="h-3 w-3" aria-hidden="true" />}</span>}
                    </div>)}</div>) : <span className="text-[11px] text-zinc-400">Unassigned</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[11px] text-zinc-400">
                    {proxy.latency_ms !== null ? `${proxy.latency_ms} ms` : '—'}
                  </td>
                  <td className="px-3 py-2.5"><ProxyQualityCell proxy={proxy} checking={checkingIds.has(proxy.id)} /></td>
                  <td className="px-3 py-2.5">
                    <div className="flex justify-end gap-1">
                      <button onClick={() => void checkProxies([proxy.id])} disabled={busy}
                        aria-label={`Check ${proxy.host}:${proxy.port}`} title="Check connection, Facebook, exit IP and speed"
                        className="rounded p-1.5 text-blue-300 hover:bg-zinc-800 disabled:opacity-30">
                        <Gauge className="h-3.5 w-3.5" />
                      </button>
                      <button onClick={() => handleTestPing(proxy.id)} disabled={busy || testingId === proxy.id}
                        aria-label={`Test ${proxy.host}:${proxy.port}`} title="Test connection"
                        className="rounded p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-30">
                        {testingId === proxy.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Activity className="h-3.5 w-3.5" />}
                      </button>
                      <button onClick={() => handleGeoCheck(proxy.id)} disabled={busy || geoCheckingId === proxy.id}
                        aria-label={`Check location for ${proxy.host}:${proxy.port}`} title="Check exit location and timezone"
                        className="rounded p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-blue-300 disabled:opacity-30">
                        {geoCheckingId === proxy.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MapPin className="h-3.5 w-3.5" />}
                      </button>
                      <button onClick={() => handleDelete([proxy.id])} disabled={busy || inUse(proxy)}
                        aria-label={`Delete ${proxy.host}:${proxy.port}`} title={inUse(proxy) ? 'Move its accounts to other proxies before deleting it' : 'Delete proxy'}
                        className="rounded p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-red-400 disabled:opacity-30">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ); })}
              </React.Fragment>)}
              {visibleProxies.length === 0 && (
                <tr><td colSpan={7} className="py-16 text-center">
                  <p className="text-xs text-zinc-400">{proxies.length === 0 ? 'Your proxy pool is empty.' : sourceProxies.length === 0 ? `${source?.name ?? 'This source'} has no proxies yet.` : folder !== ALL_FOLDERS ? 'No proxies in this folder match your filters.' : 'No proxies match your search.'}</p>
                  {sourceProxies.length === 0 ? (
                    source?.uses_api
                      ? <button onClick={() => void handleSync()} disabled={busy} className="mt-3 text-xs text-blue-300 hover:text-blue-200">Sync {source.name} from Webshare</button>
                      : <button onClick={openImport} disabled={busy || !importable.length} className="mt-3 text-xs text-blue-300 hover:text-blue-200">{source ? `Import proxies into ${source.name}` : 'Import your first proxies'}</button>
                  ) : <button onClick={() => { setSearch(''); setFilter('all'); location.reset(); setFolder(ALL_FOLDERS); }} className="mt-3 text-xs text-blue-300 hover:text-blue-200">Clear filters</button>}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div role="status" className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-[10px] text-zinc-500">
          <span>{visibleProxies.length} of {sourceProxies.length} proxies{selectedIds.size > 0 ? ` · ${selectedIds.size} selected${selectedIds.size > selectedVisible ? ` (${selectedIds.size - selectedVisible} outside this view)` : ''}` : ''}</span>
          <span>Automatic folders · A check uses up to 6 MB per proxy · Assigned proxies stay locked.</span>
        </div>
      </div>

      </div>
      {editing && <ProxySourceDialog source={editing === 'new' ? null : editing} globalLimit={globalLimit} onClose={() => setEditing(null)}
        onSaved={saved => {
          // Shown at once, so opening a new source doesn't wait for the list to reload.
          if (saved) setSources(current => [...current.filter(s => s.id !== saved.id), saved]);
          if (saved && editing === 'new') { setSourceId(saved.id); setFolder(ALL_FOLDERS); }
          void loadSources(); onRefresh();
        }} />}
      {/* Import Modal */}
      {isImportOpen && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-surface border border-border w-full max-w-lg rounded-xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between">
              <h2 className="text-sm font-semibold text-zinc-100">Import proxies</h2>
              <button
                disabled={isImporting}
                onClick={() => setIsImportOpen(false)}
                className="p-1 rounded text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleImport} className="p-5 space-y-4 text-xs">
              <p className="text-zinc-500">New proxies appear in Available with an Untested badge. Run a download/upload check to measure their quality.</p>
              <div className="space-y-1.5">
                <span className="block font-medium text-zinc-400">Source</span>
                <Select value={importSource} onValueChange={setImportSource} ariaLabel="Import into source" disabled={isImporting}
                  options={importable.map(s => ({ value: s.id, label: `${s.name} · ${s.provider_label} · ${s.protocol.toUpperCase()}` }))} />
                <p className="text-[11px] text-zinc-500">They get this source's protocol and limit. A proxy already in the pool is skipped.
                  {importable.length < sources.length ? ' Sources with an API key aren\'t listed: they sync from their provider.' : ''}</p>
              </div>
              <div>
                <label className="block text-zinc-400 font-medium mb-1.5">
                  Paste Proxies (one per line):
                </label>
                <p className="text-[11px] text-zinc-500 mb-2">
                  Format: <code className="text-zinc-300">ip:port:username:password</code> or{' '}
                  <code className="text-zinc-300">ip:port</code>
                </p>
                <textarea
                  rows={8}
                  required
                  value={importText}
                  onChange={(e) => setImportText(e.target.value)}
                  placeholder="203.0.113.10:5859:username:password&#10;198.51.100.20:6416:username:password"
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-md p-3 text-zinc-200 font-mono text-xs placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700"
                />
              </div>

              <div className="pt-3 border-t border-border flex items-center justify-end gap-2">
                <button
                  type="button"
                  disabled={isImporting}
                onClick={() => setIsImportOpen(false)}
                  className="px-3 py-1.5 rounded-md bg-zinc-900 border border-zinc-800 text-zinc-300 hover:bg-zinc-800 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isImporting || !importSource}
                  className="px-4 py-1.5 rounded-md bg-white text-zinc-950 font-semibold hover:bg-zinc-200 transition-colors shadow-sm disabled:opacity-50"
                >
                  {isImporting ? 'Importing…' : 'Add to source'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

/** "12 more are in Uploaded list and 3 in Other": where proxies listed by a Webshare source sit now. */
function countBySource(items: ProxyElsewhere[]): string {
  const counts = new Map<string, number>();
  items.forEach(item => counts.set(item.from_source, (counts.get(item.from_source) ?? 0) + 1));
  const parts = [...counts].map(([name, count]) => `${count} in ${name}`);
  const listed = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0];
  return listed.replace(/^(\d+) in /, (_, count) => `${count} more ${count === '1' ? 'is' : 'are'} in `);
}

/** Why proxies this source lists stay in another source, grouped by reason. */
function keptNote(kept: ProxyElsewhere[]): string {
  if (!kept.length) return '';
  const reasons = new Map<string, number>();
  kept.forEach(item => reasons.set(item.reason || 'it can’t move', (reasons.get(item.reason || 'it can’t move') ?? 0) + 1));
  return ' ' + [...reasons].map(([reason, count]) => `${count} stayed where ${count === 1 ? 'it is' : 'they are'}: ${reason}.`).join(' ');
}
