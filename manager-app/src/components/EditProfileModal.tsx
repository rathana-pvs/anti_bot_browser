import React, { useState, useEffect, useRef } from 'react';
import { Profile } from '../types/profile';
import { ProxyItem } from '../types/proxy';
import { SCREEN_RESOLUTIONS } from '../services/fingerprintPool';
import { X, Globe, Calendar, Monitor, FileText, Shield } from 'lucide-react';

interface EditProfileModalProps {
  profile: Profile | null;
  isOpen: boolean;
  onClose: () => void;
  onSave: (profileId: string, updates: Partial<Profile>) => Promise<void>;
  proxies?: ProxyItem[];
}

export const EditProfileModal: React.FC<EditProfileModalProps> = ({
  profile,
  isOpen,
  onClose,
  onSave,
  proxies = [],
}) => {
  const [name, setName] = useState('');
  const [resolution, setResolution] = useState('1920x1080');
  const [timezone, setTimezone] = useState('America/Los_Angeles');
  const [selectedProxyMode, setSelectedProxyMode] = useState<string>('none');
  const [proxyHost, setProxyHost] = useState('');
  const [proxyPort, setProxyPort] = useState('1080');
  const [proxyUser, setProxyUser] = useState('');
  const [proxyPass, setProxyPass] = useState('');

  const [warmingWeek, setWarmingWeek] = useState(1);
  const [warmingComplete, setWarmingComplete] = useState(false);
  const [notes, setNotes] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const lastInitializedIdRef = useRef<string | null>(null);

  // Initialize form ONLY once when modal opens or profile ID changes
  useEffect(() => {
    if (!isOpen || !profile) {
      lastInitializedIdRef.current = null;
      return;
    }

    if (lastInitializedIdRef.current === profile.id) {
      return;
    }
    lastInitializedIdRef.current = profile.id;

    setName(profile.name);
    setResolution(profile.fingerprint.screen_resolution || '1920x1080');
    setTimezone(profile.fingerprint.timezone || 'America/Los_Angeles');
    setWarmingWeek(profile.account.warming_week || 1);
    setWarmingComplete(profile.account.warming_complete || false);
    setNotes(profile.account.notes || '');

    const boundProxy = proxies.find(
      (p) =>
        p.profile_id === profile.id ||
        (p.host === profile.network.proxy_host && Number(p.port) === Number(profile.network.proxy_port))
    );

    if (boundProxy) {
      setSelectedProxyMode(boundProxy.id);
      setProxyHost(boundProxy.host);
      setProxyPort(String(boundProxy.port));
      setProxyUser(boundProxy.username || '');
      setProxyPass(boundProxy.password || '');
      if (boundProxy.timezone) {
        setTimezone(boundProxy.timezone);
      }
    } else if (profile.network.proxy_host) {
      setSelectedProxyMode('custom');
      setProxyHost(profile.network.proxy_host);
      setProxyPort(String(profile.network.proxy_port || 1080));
      setProxyUser(profile.network.proxy_user || '');
      setProxyPass(profile.network.proxy_pass || '');
    } else {
      setSelectedProxyMode('none');
      setProxyHost('');
      setProxyPort('1080');
      setProxyUser('');
      setProxyPass('');
    }
  }, [isOpen, profile?.id]);

  if (!isOpen || !profile) return null;

  // Find proxy currently active in pool for this modal
  const selectedProxyItem = proxies.find((p) => p.id === selectedProxyMode);
  const currentPoolProxy = proxies.find(
    (p) =>
      p.profile_id === profile.id ||
      (p.host === profile.network.proxy_host && Number(p.port) === Number(profile.network.proxy_port))
  );

  const otherAvailableProxies = proxies.filter(
    (p) => !p.assigned && p.id !== currentPoolProxy?.id && p.id !== selectedProxyItem?.id
  );

  const handleProxyChange = (mode: string) => {
    setSelectedProxyMode(mode);
    if (mode === 'none') {
      setProxyHost('');
      setProxyPort('1080');
      setProxyUser('');
      setProxyPass('');
    } else if (mode === 'custom') {
      // keep current or clear
    } else {
      const selected = proxies.find((p) => p.id === mode);
      if (selected) {
        setProxyHost(selected.host);
        setProxyPort(String(selected.port));
        setProxyUser(selected.username || '');
        setProxyPass(selected.password || '');
        if (selected.timezone) {
          setTimezone(selected.timezone);
        }
      }
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);

    try {
      const updates: Partial<Profile> = {
        name,
        fingerprint: {
          ...profile.fingerprint,
          screen_resolution: resolution,
          timezone,
        },
        network: {
          ...profile.network,
          proxy_host: proxyHost.trim(),
          proxy_port: parseInt(proxyPort, 10) || 1080,
          proxy_user: proxyUser.trim(),
          proxy_pass: proxyPass.trim(),
        },
        account: {
          ...profile.account,
          warming_week: Number(warmingWeek),
          warming_complete: warmingComplete,
          notes: notes.trim(),
        },
      };

      await onSave(profile.id, updates);
      onClose();
    } catch (err: any) {
      alert(`Save error: ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 select-none">
      <div className="bg-surface border border-border w-full max-w-lg rounded-xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Edit Profile</h2>
            <p className="text-[11px] text-zinc-500 font-mono mt-0.5">{profile.id}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4 text-xs max-h-[80vh] overflow-y-auto">
          {/* Profile Name */}
          <div>
            <label className="block text-zinc-400 font-medium mb-1.5">Profile Name</label>
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:border-zinc-700"
            />
          </div>

          {/* Screen Resolution */}
          <div>
            <label className="block text-zinc-400 font-medium mb-1.5 flex items-center gap-1.5">
              <Monitor className="w-3.5 h-3.5 text-zinc-500" />
              Screen Resolution
            </label>
            <select
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              className="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-700 font-mono"
            >
              {SCREEN_RESOLUTIONS.map((res) => (
                <option key={res} value={res}>
                  {res} {res === '1920x1080' ? '(Recommended Standard)' : ''}
                </option>
              ))}
            </select>
          </div>

          {/* Dedicated Proxy Selection */}
          <div className="p-3 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="font-medium text-zinc-300 flex items-center gap-1.5">
                <Globe className="w-3.5 h-3.5 text-zinc-400" />
                Proxy Assignment
              </span>
              <span className="text-[10px] text-zinc-400 font-mono bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">
                {otherAvailableProxies.length + (selectedProxyItem ? 1 : 0)} in pool
              </span>
            </div>

            <div>
              <label className="text-[11px] text-zinc-400 block mb-1">Select Available Proxy</label>
              <select
                value={selectedProxyMode}
                onChange={(e) => handleProxyChange(e.target.value)}
                className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-2 text-zinc-100 focus:outline-none focus:border-zinc-700 font-mono text-xs"
              >
                {/* Currently Assigned Proxy */}
                {currentPoolProxy && (
                  <optgroup label="Currently Assigned">
                    <option value={currentPoolProxy.id}>
                      {currentPoolProxy.host}:{currentPoolProxy.port} {currentPoolProxy.latency_ms ? `(${currentPoolProxy.latency_ms}ms)` : ''} — Current Proxy
                    </option>
                  </optgroup>
                )}

                {/* Selected Proxy from pool if different from currently assigned */}
                {selectedProxyItem && selectedProxyItem.id !== currentPoolProxy?.id && (
                  <optgroup label="Selected Proxy">
                    <option value={selectedProxyItem.id}>
                      {selectedProxyItem.host}:{selectedProxyItem.port} {selectedProxyItem.latency_ms ? `(${selectedProxyItem.latency_ms}ms)` : ''} — Selected
                    </option>
                  </optgroup>
                )}

                {/* Other Available unassigned proxies in pool */}
                {otherAvailableProxies.length > 0 && (
                  <optgroup label={`Available in Pool (${otherAvailableProxies.length})`}>
                    {otherAvailableProxies.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.host}:{p.port} {p.latency_ms ? `(${p.latency_ms}ms)` : ''} — Available
                      </option>
                    ))}
                  </optgroup>
                )}

                <option value="none">Direct Connection (No Proxy)</option>
                <option value="custom">Custom / Manual Proxy Entry...</option>
              </select>
            </div>

            {/* Active pool selection info */}
            {selectedProxyItem && (
              <div className="p-2.5 rounded bg-zinc-900/60 border border-zinc-800/80 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0"></span>
                    <div>
                      <div className="font-mono text-xs text-zinc-200">
                        {selectedProxyItem.host}:{selectedProxyItem.port}
                      </div>
                      <div className="text-[10px] text-zinc-400">
                        {selectedProxyItem.city && selectedProxyItem.region
                          ? `${selectedProxyItem.city}, ${selectedProxyItem.region} (${selectedProxyItem.country_code || 'US'})`
                          : (selectedProxyItem.username ? `Auth: ${selectedProxyItem.username}` : 'No auth required')}
                      </div>
                    </div>
                  </div>
                  {selectedProxyItem.latency_ms && (
                    <div className="text-[11px] font-mono text-zinc-400 bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">
                      {selectedProxyItem.latency_ms}ms
                    </div>
                  )}
                </div>

                <div className="flex items-center justify-between pt-1 border-t border-zinc-800/60 text-[10px]">
                  <span className="text-zinc-400 flex items-center gap-1 font-mono">
                    <span className="text-zinc-500">TZ:</span> {selectedProxyItem.timezone || timezone}
                  </span>
                  <span className="text-emerald-400 font-medium flex items-center gap-1">
                    <Shield className="w-3 h-3 text-emerald-500" />
                    Killswitch: Active
                  </span>
                </div>
              </div>
            )}

            {/* Manual input section (if custom selected) */}
            {selectedProxyMode === 'custom' && (
              <div className="space-y-2 pt-1 border-t border-zinc-800/60">
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2">
                    <input
                      type="text"
                      required
                      value={proxyHost}
                      onChange={(e) => setProxyHost(e.target.value)}
                      placeholder="Proxy Host / IP"
                      className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700 font-mono"
                    />
                  </div>
                  <div>
                    <input
                      type="number"
                      required
                      value={proxyPort}
                      onChange={(e) => setProxyPort(e.target.value)}
                      placeholder="Port (1080)"
                      className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700 font-mono"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={proxyUser}
                    onChange={(e) => setProxyUser(e.target.value)}
                    placeholder="Username (optional)"
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700"
                  />
                  <input
                    type="password"
                    value={proxyPass}
                    onChange={(e) => setProxyPass(e.target.value)}
                    placeholder="Password (optional)"
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Account Warming */}
          <div className="p-3 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2.5">
            <span className="font-medium text-zinc-300 flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5 text-amber-500" />
              Warming Schedule
            </span>

            <div className="grid grid-cols-2 gap-3 items-center">
              <div>
                <label className="text-[11px] text-zinc-400 block mb-1">Current Week</label>
                <select
                  value={warmingWeek}
                  onChange={(e) => setWarmingWeek(Number(e.target.value))}
                  className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 focus:outline-none focus:border-zinc-700"
                >
                  <option value={1}>Week 1 (Manual only)</option>
                  <option value={2}>Week 2 (Browsing/friends)</option>
                  <option value={3}>Week 3 (Light engagement)</option>
                  <option value={4}>Week 4+ (Automation ready)</option>
                </select>
              </div>

              <div className="pt-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={warmingComplete}
                    onChange={(e) => setWarmingComplete(e.target.checked)}
                    className="rounded bg-zinc-900 border-zinc-800 text-white focus:ring-0 focus:ring-offset-0"
                  />
                  <span className="text-zinc-300 text-xs">Warming Completed</span>
                </label>
              </div>
            </div>
          </div>

          {/* Account Notes */}
          <div>
            <label className="block text-zinc-400 font-medium mb-1.5 flex items-center gap-1.5">
              <FileText className="w-3.5 h-3.5 text-zinc-500" />
              Notes & Metadata
            </label>
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Account credentials, purpose, warming notes..."
              className="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:border-zinc-700 resize-none font-mono text-[11px]"
            />
          </div>

          {/* Footer buttons */}
          <div className="pt-3 border-t border-border flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isSaving}
              className="px-3 py-1.5 rounded-md bg-zinc-900 border border-zinc-800 text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSaving}
              className="px-4 py-1.5 rounded-md bg-white text-zinc-950 font-semibold hover:bg-zinc-200 transition-colors shadow-sm disabled:opacity-50"
            >
              {isSaving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
