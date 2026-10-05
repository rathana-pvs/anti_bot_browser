import React, { useEffect, useState } from 'react';
import { formatProxyGeography, ProxyItem } from '../types/proxy';
import { checkProxyGeography, importProxies, refreshProxyStatuses, testProxyPing, deleteSelectedProxies } from '../services/api';
import { Globe, Plus, Trash2, Activity, MapPin, X, PanelLeftOpen, Loader2, Search, Lock } from 'lucide-react';
import { useAppDialog } from './ui/AppDialogProvider';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';

interface ProxyPanelProps {
  proxies: ProxyItem[];
  onRefresh: () => void;
  onSelectProfile?: (profileId: string) => void;
  isSidebarOpen?: boolean;
  onToggleSidebar?: () => void;
}

export const ProxyPanel: React.FC<ProxyPanelProps> = ({
  proxies,
  onRefresh,
  onSelectProfile,
  isSidebarOpen = true,
  onToggleSidebar,
}) => {
  const { showAlert, showConfirm } = useAppDialog();
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [isImporting, setIsImporting] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [geoCheckingId, setGeoCheckingId] = useState<string | null>(null);
  const [isRefreshingStatus, setIsRefreshingStatus] = useState(false);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [isDeleting, setIsDeleting] = useState(false);
  const busy = isDeleting || isImporting || isRefreshingStatus;
  const inUse = (proxy: ProxyItem) => Boolean(proxy.assigned || proxy.profile_id);
  const assigned = proxies.filter(inUse).length;
  const available = proxies.length - assigned;
  const query = search.trim().toLowerCase();
  const visibleProxies = proxies.filter((proxy) => {
    if (filter === 'available' && inUse(proxy)) return false;
    if (filter === 'assigned' && !inUse(proxy)) return false;
    return !query || [proxy.host, String(proxy.port), proxy.username, proxy.profile_id,
      proxy.exit_ip, formatProxyGeography(proxy)].filter(Boolean).join(' ').toLowerCase().includes(query);
  }).sort((a, b) => Number(inUse(b)) - Number(inUse(a)));
  const selectableIds = visibleProxies.filter((proxy) => !inUse(proxy)).map((proxy) => proxy.id);
  const selectedVisible = selectableIds.filter((id) => selectedIds.has(id)).length;
  const allVisibleSelected = selectableIds.length > 0 && selectedVisible === selectableIds.length;

  useEffect(() => {
    const availableIds = new Set(proxies.filter((proxy) => !proxy.assigned && !proxy.profile_id).map((proxy) => proxy.id));
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

  const handleImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importText.trim()) return;

    setIsImporting(true);
    try {
      const result = await importProxies(importText);
      await showAlert(`Added ${result.added} proxies (${result.total} total). Online: ${result.online}; offline: ${result.offline}; locations found: ${result.geo_checked}.`, { title: 'Proxy import complete' });
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
      const removed = new Set([...result.deleted_ids, ...result.missing_ids]);
      setSelectedIds((current) => new Set([...current].filter((id) => !removed.has(id))));
      onRefresh();
      if (result.blocked_ids.length) {
        await showAlert(`Deleted ${result.deleted_ids.length} proxies. ${result.blocked_ids.length} assigned proxies were kept.`, {
          title: 'Proxy selection updated', variant: 'warning',
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

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col gap-4 overflow-hidden bg-background p-4 sm:p-5 select-none">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {!isSidebarOpen && onToggleSidebar && (
            <button onClick={onToggleSidebar} aria-label="Expand sidebar"
              className="rounded-md border border-border p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200">
              <PanelLeftOpen className="h-4 w-4" />
            </button>
          )}
          <div>
            <h1 className="flex items-center gap-2 text-base font-semibold text-zinc-100"><Globe className="h-4 w-4 text-zinc-400" />Proxy Pool</h1>
            <p className="mt-1 text-xs text-zinc-500">Manage connections for your profiles.</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleRefreshStatus} disabled={busy || proxies.length === 0}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
            {isRefreshingStatus ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Activity className="h-3.5 w-3.5" />}
            {isRefreshingStatus ? 'Checking…' : 'Check status'}
          </button>
          <button onClick={() => setIsImportOpen(true)} disabled={busy}
            className="flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-zinc-200 disabled:opacity-40">
            <Plus className="h-3.5 w-3.5" />Import proxies
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-zinc-500">
        <span><strong className="font-medium text-zinc-200">{proxies.length}</strong> total</span>
        <span><strong className="font-medium text-emerald-400">{available}</strong> available</span>
        <span><strong className="font-medium text-zinc-300">{assigned}</strong> assigned</span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="relative min-w-40 flex-1">
            <Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
            <input value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Search proxies"
              placeholder="Search host, location, or profile…"
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 py-2 pl-8 pr-3 text-xs text-zinc-200 outline-none focus:border-blue-500" />
          </div>
          <div className="w-36">
            <Select value={filter} onValueChange={setFilter} ariaLabel="Filter proxies" className="min-h-8 py-2"
              options={[{ value: 'all', label: 'All proxies' }, { value: 'available', label: 'Available' }, { value: 'assigned', label: 'Assigned' }]} />
          </div>
          {selectedIds.size > 0 && (
            <>
              <button onClick={() => setSelectedIds(new Set())} disabled={busy} className="px-2 py-1.5 text-xs text-zinc-400 hover:text-white disabled:opacity-40">Clear selection</button>
              <button onClick={() => handleDelete([...selectedIds])} disabled={busy}
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
                  <Checkbox ariaLabel="Select all available proxies in view" disabled={busy || selectableIds.length === 0}
                    checked={allVisibleSelected ? true : selectedVisible > 0 ? 'indeterminate' : false}
                    onCheckedChange={(checked) => toggleSelection(selectableIds, checked)} />
                </th>
                <th className="px-3 py-2.5 font-medium">Proxy</th>
                <th className="px-3 py-2.5 font-medium">Location / timezone</th>
                <th className="px-3 py-2.5 font-medium">Assignment</th>
                <th className="px-3 py-2.5 font-medium">Latency</th>
                <th className="w-28 px-3 py-2.5 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {visibleProxies.map((proxy) => (
                <tr key={proxy.id} className={selectedIds.has(proxy.id) ? 'bg-blue-500/5' : 'hover:bg-zinc-800/30'}>
                  <td className="py-2.5 pl-3" title={inUse(proxy) ? 'Assigned proxies cannot be deleted' : undefined}>
                    <Checkbox ariaLabel={`Select ${proxy.host}:${proxy.port}`} checked={selectedIds.has(proxy.id)}
                      disabled={busy || inUse(proxy)} onCheckedChange={(checked) => toggleSelection([proxy.id], checked)} />
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <span role="img" aria-label={proxy.reachable === true ? 'Online' : proxy.reachable === false ? 'Offline' : 'Status unknown'}
                        title={proxy.last_checked ? `Last checked ${new Date(proxy.last_checked).toLocaleString()}` : 'Not checked yet'}
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${proxy.reachable === true ? 'bg-emerald-400' : proxy.reachable === false ? 'bg-red-400' : 'bg-zinc-600'}`} />
                      <span className="select-text font-mono text-[11px] text-zinc-200">{proxy.host}:{proxy.port}</span>
                    </div>
                    <p className="mt-1 pl-3.5 text-[10px] text-zinc-500">{proxy.username || 'No authentication'} · {proxy.type.toUpperCase()}</p>
                  </td>
                  <td className="max-w-64 px-3 py-2.5" title={proxy.geo_last_error || formatProxyGeography(proxy)}>
                    <p className="truncate text-[11px] text-zinc-300">{proxy.geo_source === 'unknown' ? 'Location unknown' : [proxy.city, proxy.country_code || proxy.country].filter(Boolean).join(', ') || 'Location unknown'}</p>
                    <p className="mt-1 truncate text-[10px] text-zinc-500">{proxy.geo_source !== 'unknown' && proxy.timezone ? proxy.timezone : 'Timezone not detected'}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    {inUse(proxy) ? (
                      <button disabled={!proxy.profile_id || !onSelectProfile} onClick={() => proxy.profile_id && onSelectProfile?.(proxy.profile_id)}
                        className="inline-flex items-center gap-1.5 text-[11px] text-blue-300 hover:text-blue-200 disabled:text-zinc-400">
                        <Lock className="h-3 w-3" />{proxy.profile_id || 'Assigned'}
                      </button>
                    ) : <span className="text-[11px] text-zinc-400">Available</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[11px] text-zinc-400">
                    {proxy.latency_ms !== null ? `${proxy.latency_ms} ms` : '—'}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex justify-end gap-1">
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
                        aria-label={`Delete ${proxy.host}:${proxy.port}`} title={inUse(proxy) ? 'Assigned proxies cannot be deleted' : 'Delete proxy'}
                        className="rounded p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-red-400 disabled:opacity-30">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {visibleProxies.length === 0 && (
                <tr><td colSpan={6} className="py-16 text-center">
                  <p className="text-xs text-zinc-400">{proxies.length === 0 ? 'Your proxy pool is empty.' : 'No proxies match your search.'}</p>
                  {proxies.length === 0 ? (
                    <button onClick={() => setIsImportOpen(true)} disabled={busy} className="mt-3 text-xs text-blue-300 hover:text-blue-200">Import your first proxies</button>
                  ) : <button onClick={() => { setSearch(''); setFilter('all'); }} className="mt-3 text-xs text-blue-300 hover:text-blue-200">Clear filters</button>}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div role="status" className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-[10px] text-zinc-500">
          <span>{visibleProxies.length} of {proxies.length} proxies{selectedIds.size > 0 ? ` · ${selectedIds.size} selected${selectedIds.size > selectedVisible ? ` (${selectedIds.size - selectedVisible} outside this view)` : ''}` : ''}</span>
          <span>Assigned proxies are protected.</span>
        </div>
      </div>

      {/* Import Modal */}
      {isImportOpen && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-surface border border-border w-full max-w-lg rounded-xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between">
              <h2 className="text-sm font-semibold text-zinc-100">Import Proxy List</h2>
              <button
                disabled={isImporting}
                onClick={() => setIsImportOpen(false)}
                className="p-1 rounded text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleImport} className="p-5 space-y-4 text-xs">
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
                  disabled={isImporting}
                  className="px-4 py-1.5 rounded-md bg-white text-zinc-950 font-semibold hover:bg-zinc-200 transition-colors shadow-sm disabled:opacity-50"
                >
                  {isImporting ? 'Importing...' : 'Add to Pool'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
