import React, { useState } from 'react';
import { ProxyItem } from '../types/proxy';
import { importProxies, testProxyPing, deleteProxy } from '../services/api';
import { Globe, Plus, Trash2, Activity, X, PanelLeftOpen } from 'lucide-react';

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
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [isImporting, setIsImporting] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);

  const total = proxies.length;
  const assigned = proxies.filter((p) => p.assigned).length;
  const available = total - assigned;

  const handleImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importText.trim()) return;

    setIsImporting(true);
    try {
      const result = await importProxies(importText);
      alert(`Successfully added ${result.added} new proxies (${result.total} total in pool)`);
      setImportText('');
      setIsImportOpen(false);
      onRefresh();
    } catch (err: any) {
      alert(`Import failed: ${err.message}`);
    } finally {
      setIsImporting(false);
    }
  };

  const handleTestPing = async (proxyId: string) => {
    setTestingId(proxyId);
    try {
      await testProxyPing(proxyId);
      onRefresh();
    } catch (err: any) {
      alert(`Ping test failed: ${err.message}`);
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async (proxyId: string) => {
    if (confirm('Are you sure you want to remove this proxy from the pool?')) {
      try {
        await deleteProxy(proxyId);
        onRefresh();
      } catch (err: any) {
        alert(`Delete failed: ${err.message}`);
      }
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden p-6 select-none">
      {/* Top Header */}
      <div className="flex items-center justify-between pb-6 border-b border-border">
        <div className="flex items-center gap-3">
          {!isSidebarOpen && onToggleSidebar && (
            <button
              onClick={onToggleSidebar}
              className="p-2 rounded-md bg-surface border border-border text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
              title="Expand Sidebar"
            >
              <PanelLeftOpen className="w-4 h-4" />
            </button>
          )}
          <div>
            <h1 className="text-lg font-semibold text-zinc-100 flex items-center gap-2">
              <Globe className="w-5 h-5 text-zinc-400" />
              Residential Proxy Pool
            </h1>
            <p className="text-xs text-zinc-400 mt-1">
              Dedicated SOCKS5 residential proxies with fail-closed tun2socks kill switch protection.
            </p>
          </div>
        </div>

        <button
          onClick={() => setIsImportOpen(true)}
          className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-white text-zinc-950 font-semibold text-xs hover:bg-zinc-200 transition-colors shadow-sm"
        >
          <Plus className="w-4 h-4 stroke-[2.5]" />
          <span>Import Proxies</span>
        </button>
      </div>

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-3 gap-4 my-6">
        <div className="p-4 rounded-xl bg-surface border border-border">
          <span className="text-xs text-zinc-500 font-medium block">TOTAL POOL</span>
          <span className="text-2xl font-bold text-zinc-100 font-mono mt-1 block">{total}</span>
        </div>
        <div className="p-4 rounded-xl bg-surface border border-border">
          <span className="text-xs text-zinc-500 font-medium block">ASSIGNED (ACTIVE)</span>
          <span className="text-2xl font-bold text-green-400 font-mono mt-1 block">{assigned}</span>
        </div>
        <div className="p-4 rounded-xl bg-surface border border-border">
          <span className="text-xs text-zinc-500 font-medium block">AVAILABLE POOL</span>
          <span className="text-2xl font-bold text-zinc-100 font-mono mt-1 block">{available}</span>
        </div>
      </div>

      {/* Proxies Table */}
      <div className="flex-1 rounded-xl bg-surface border border-border overflow-hidden flex flex-col">
        <div className="px-4 py-3 border-b border-border bg-zinc-900/50 flex items-center justify-between text-xs font-semibold text-zinc-400 uppercase tracking-wider">
          <span>Host & Port</span>
          <span>Status</span>
          <span>Latency</span>
          <span>Actions</span>
        </div>

        <div className="flex-1 overflow-y-auto divide-y divide-border/60">
          {proxies.length === 0 ? (
            <div className="py-16 text-center text-zinc-500 text-xs">
              No proxies in pool. Click "Import Proxies" to upload a list.
            </div>
          ) : (
            proxies.map((proxy) => (
              <div
                key={proxy.id}
                className="px-4 py-3 flex items-center justify-between text-xs hover:bg-zinc-800/40 transition-colors"
              >
                {/* Host & Port */}
                <div className="flex items-center gap-3">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      proxy.assigned ? 'bg-green-500' : 'bg-zinc-500'
                    }`}
                  />
                  <div>
                    <span className="font-mono text-zinc-200 font-medium">
                      {proxy.host}:{proxy.port}
                    </span>
                    <span className="text-[11px] text-zinc-500 block">
                      {proxy.username ? `User: ${proxy.username}` : 'No Auth'} · SOCKS5
                    </span>
                  </div>
                </div>

                {/* Status */}
                <div>
                  {proxy.assigned ? (
                    <button
                      onClick={() => proxy.profile_id && onSelectProfile?.(proxy.profile_id)}
                      className="px-2 py-0.5 rounded bg-zinc-800 border border-green-800/40 text-green-400 font-mono text-[11px] hover:border-green-700 transition-colors"
                    >
                      Assigned → {proxy.profile_id}
                    </button>
                  ) : (
                    <span className="px-2 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-400 font-mono text-[11px]">
                      Available
                    </span>
                  )}
                </div>

                {/* Latency */}
                <div className="flex items-center gap-2">
                  {proxy.latency_ms !== null ? (
                    <span
                      className={`font-mono text-[11px] ${
                        proxy.latency_ms < 100
                          ? 'text-green-400'
                          : proxy.latency_ms < 250
                          ? 'text-amber-400'
                          : 'text-zinc-400'
                      }`}
                    >
                      {proxy.latency_ms} ms
                    </span>
                  ) : (
                    <span className="text-zinc-600 font-mono text-[11px]">—</span>
                  )}
                  <button
                    onClick={() => handleTestPing(proxy.id)}
                    disabled={testingId === proxy.id}
                    className="p-1 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors disabled:opacity-50"
                    title="Test ping"
                  >
                    <Activity className={`w-3.5 h-3.5 ${testingId === proxy.id ? 'animate-spin' : ''}`} />
                  </button>
                </div>

                {/* Delete */}
                <div>
                  {!proxy.assigned ? (
                    <button
                      onClick={() => handleDelete(proxy.id)}
                      className="p-1 rounded text-zinc-500 hover:text-red-400 hover:bg-zinc-800 transition-colors"
                      title="Remove proxy"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  ) : (
                    <span className="text-zinc-600 text-[11px]">Locked</span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Import Modal */}
      {isImportOpen && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-surface border border-border w-full max-w-lg rounded-xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between">
              <h2 className="text-sm font-semibold text-zinc-100">Import Proxy List</h2>
              <button
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
                  placeholder="45.58.228.187:5859:username:password&#10;9.142.35.245:6416:username:password"
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-md p-3 text-zinc-200 font-mono text-xs placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700"
                />
              </div>

              <div className="pt-3 border-t border-border flex items-center justify-end gap-2">
                <button
                  type="button"
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
