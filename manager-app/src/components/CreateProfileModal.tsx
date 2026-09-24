import React, { useState, useEffect, useRef } from 'react';
import { Profile } from '../types/profile';
import { ProxyItem } from '../types/proxy';
import {
  generateAuthenticLinuxFingerprint,
  RESOLUTION_OPTIONS,
  REAL_HOST_SPECS,
} from '../services/fingerprintPool';
import { X, Shield, Globe, Monitor, Cpu } from 'lucide-react';

interface CreateProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (profile: Partial<Profile>) => void;
  existingCount: number;
  proxies?: ProxyItem[];
}

export const CreateProfileModal: React.FC<CreateProfileModalProps> = ({
  isOpen,
  onClose,
  onCreate,
  existingCount,
  proxies = [],
}) => {
  const [name, setName] = useState(`Account ${existingCount + 1}`);
  const [selectedResolution, setSelectedResolution] = useState('1920x1080');
  const [fingerprint, setFingerprint] = useState(
    generateAuthenticLinuxFingerprint(existingCount + 1, '1920x1080')
  );

  const [selectedProxyMode, setSelectedProxyMode] = useState<string>('none');
  const [proxyHost, setProxyHost] = useState('');
  const [proxyPort, setProxyPort] = useState('1080');
  const [proxyUser, setProxyUser] = useState('');
  const [proxyPass, setProxyPass] = useState('');

  const lastOpenedRef = useRef(false);

  // Set default proxy and reset form ONLY once when modal opens from closed state
  useEffect(() => {
    if (!isOpen) {
      lastOpenedRef.current = false;
      return;
    }

    if (lastOpenedRef.current) return;
    lastOpenedRef.current = true;

    setName(`Account ${existingCount + 1}`);
    setSelectedResolution('1920x1080');
    setFingerprint(generateAuthenticLinuxFingerprint(existingCount + 1, '1920x1080'));

    const unassigned = proxies.filter((p) => !p.assigned);
    if (unassigned.length > 0) {
      const first = unassigned[0];
      setSelectedProxyMode(first.id);
      setProxyHost(first.host);
      setProxyPort(String(first.port));
      setProxyUser(first.username || '');
      setProxyPass(first.password || '');
      if (first.timezone) {
        setFingerprint((prev) => ({
          ...prev,
          timezone: first.timezone!,
        }));
      }
    } else {
      setSelectedProxyMode('none');
      setProxyHost('');
      setProxyPort('1080');
      setProxyUser('');
      setProxyPass('');
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleResolutionChange = (res: string) => {
    setSelectedResolution(res);
    setFingerprint((prev) => ({
      ...prev,
      screen_resolution: res,
    }));
  };

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
          setFingerprint((prev) => ({
            ...prev,
            timezone: selected.timezone!,
          }));
        }
      }
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const nextIndex = existingCount + 1;
    const profileId = `profile_${String(nextIndex).padStart(3, '0')}`;
    const vncPort = 5900 + nextIndex;
    const wsPort = 6080 + nextIndex;

    const newProfile: Partial<Profile> = {
      id: profileId,
      name,
      status: 'stopped',
      fingerprint: {
        ...fingerprint,
        ...REAL_HOST_SPECS,
        screen_resolution: selectedResolution,
        timezone: fingerprint.timezone,
      },
      network: {
        proxy_type: 'socks5',
        proxy_host: proxyHost.trim(),
        proxy_port: parseInt(proxyPort, 10) || 1080,
        proxy_user: proxyUser.trim(),
        proxy_pass: proxyPass.trim(),
      },
      container: {
        id: null,
        vnc_port: vncPort,
        ws_port: wsPort,
        volume_path: `profiles/${profileId}/chrome_data`,
      },
      account: {
        platform: 'facebook',
        email: '',
        notes: '',
        warming_start_date: null,
        warming_complete: false,
        warming_week: 1,
        posts_today: 0,
        last_post_date: null,
      },
    };

    onCreate(newProfile);
    onClose();
  };

  const activeSelectedProxy = proxies.find((p) => p.id === selectedProxyMode);
  const availableProxies = proxies.filter((p) => !p.assigned && p.id !== selectedProxyMode);

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-surface border border-border w-full max-w-lg rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="px-5 py-4 border-b border-border flex items-center justify-between shrink-0">
          <h2 className="text-sm font-semibold text-zinc-100">Create Isolated Profile</h2>
          <button
            onClick={onClose}
            className="p-1 rounded text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 flex flex-col min-h-0 overflow-hidden">
          <div className="p-5 space-y-4 text-xs overflow-y-auto flex-1">
            {/* Profile Name */}
          <div>
            <label className="block text-zinc-400 font-medium mb-1.5">Profile Name</label>
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Account 2"
              className="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:border-zinc-700"
            />
          </div>

          {/* Screen Resolution Selector */}
          <div>
            <label className="block text-zinc-400 font-medium mb-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Monitor className="w-3.5 h-3.5 text-zinc-400" />
                Screen Resolution
              </span>
              <span className="text-[10px] text-zinc-500 font-normal">Customizes Canvas & Viewport</span>
            </label>
            <select
              value={selectedResolution}
              onChange={(e) => handleResolutionChange(e.target.value)}
              className="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-700 font-mono text-xs"
            >
              {RESOLUTION_OPTIONS.map((res) => (
                <option key={res.value} value={res.value}>
                  {res.label} ({res.category}){res.recommended ? ' — Recommended' : ''}
                </option>
              ))}
            </select>
          </div>

          {/* Real Device Hardware Specs (Locked) */}
          <div className="p-3 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="font-medium text-zinc-300 flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5 text-emerald-400" />
                Real Device Hardware Specs
              </span>
              <span className="flex items-center gap-1 text-[10px] text-emerald-400 font-mono bg-emerald-950/40 border border-emerald-800/60 px-2 py-0.5 rounded">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                Host Passthrough (/dev/dri)
              </span>
            </div>

            <div className="grid grid-cols-2 gap-2 text-[11px] text-zinc-400 font-mono">
              <div className="col-span-2 p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-500 block text-[10px]">GPU ACCELERATION</span>
                <span className="text-zinc-200 font-medium truncate block">
                  {REAL_HOST_SPECS.webgl_renderer}
                </span>
                <span className="text-[10px] text-zinc-500 block mt-0.5">
                  Vendor: {REAL_HOST_SPECS.webgl_vendor}
                </span>
              </div>
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-500 block text-[10px]">CPU CORES</span>
                <span className="text-zinc-200">{REAL_HOST_SPECS.hardware_concurrency} Cores</span>
              </div>
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-500 block text-[10px]">SYSTEM MEMORY</span>
                <span className="text-zinc-200">{REAL_HOST_SPECS.device_memory} GB RAM</span>
              </div>
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-500 block text-[10px]">TIMEZONE</span>
                <span className="text-zinc-200 truncate block">{fingerprint.timezone}</span>
              </div>
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-500 block text-[10px]">OS / PLATFORM</span>
                <span className="text-zinc-200">Linux x86_64</span>
              </div>
            </div>

            <p className="text-[10px] text-zinc-500 leading-relaxed pt-1 border-t border-zinc-900">
              Locked to real host TigerLake Iris Xe hardware. Prevents anti-detect shader math mismatches on Facebook.
            </p>
          </div>

          {/* Dedicated Proxy Selection */}
          <div className="p-3 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="font-medium text-zinc-300 flex items-center gap-1.5">
                <Globe className="w-3.5 h-3.5 text-zinc-400" />
                Dedicated Proxy
              </span>
              <span className="text-[10px] text-zinc-400 font-mono bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">
                {availableProxies.length + (activeSelectedProxy ? 1 : 0)} in pool
              </span>
            </div>

            <div>
              <label className="text-[11px] text-zinc-400 block mb-1">Select Available Proxy</label>
              <select
                value={selectedProxyMode}
                onChange={(e) => handleProxyChange(e.target.value)}
                className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-2 text-zinc-100 focus:outline-none focus:border-zinc-700 font-mono text-xs"
              >
                {activeSelectedProxy && (
                  <optgroup label="Selected Proxy">
                    <option value={activeSelectedProxy.id}>
                      {activeSelectedProxy.host}:{activeSelectedProxy.port} {activeSelectedProxy.latency_ms ? `(${activeSelectedProxy.latency_ms}ms)` : ''} — Selected
                    </option>
                  </optgroup>
                )}

                {availableProxies.length > 0 && (
                  <optgroup label={`Available in Pool (${availableProxies.length})`}>
                    {availableProxies.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.host}:{p.port} {p.latency_ms ? `(${p.latency_ms}ms)` : ''} — SOCKS5
                      </option>
                    ))}
                  </optgroup>
                )}

                <option value="none">Direct Connection (No Proxy)</option>
                <option value="custom">Custom / Manual Proxy Entry...</option>
              </select>
            </div>

            {/* Active pool selection info */}
            {activeSelectedProxy && (
              <div className="p-2.5 rounded bg-zinc-900/60 border border-zinc-800/80 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0"></span>
                    <div>
                      <div className="font-mono text-xs text-zinc-200">
                        {activeSelectedProxy.host}:{activeSelectedProxy.port}
                      </div>
                      <div className="text-[10px] text-zinc-400">
                        {activeSelectedProxy.city && activeSelectedProxy.region
                          ? `${activeSelectedProxy.city}, ${activeSelectedProxy.region} (${activeSelectedProxy.country_code || 'US'})`
                          : (activeSelectedProxy.username ? `Auth: ${activeSelectedProxy.username}` : 'No auth required')}
                      </div>
                    </div>
                  </div>
                  {activeSelectedProxy.latency_ms && (
                    <div className="text-[11px] font-mono text-zinc-400 bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">
                      {activeSelectedProxy.latency_ms}ms
                    </div>
                  )}
                </div>

                <div className="flex items-center justify-between pt-1 border-t border-zinc-800/60 text-[10px]">
                  <span className="text-zinc-400 flex items-center gap-1 font-mono">
                    <span className="text-zinc-500">TZ:</span> {activeSelectedProxy.timezone || fingerprint.timezone}
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
                      placeholder="IP or Hostname"
                      className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700 font-mono"
                    />
                  </div>
                  <div>
                    <input
                      type="number"
                      required
                      value={proxyPort}
                      onChange={(e) => setProxyPort(e.target.value)}
                      placeholder="Port"
                      className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700 font-mono"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={proxyUser}
                    onChange={(e) => setProxyUser(e.target.value)}
                    placeholder="Username (if needed)"
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700"
                  />
                  <input
                    type="password"
                    value={proxyPass}
                    onChange={(e) => setProxyPass(e.target.value)}
                    placeholder="Password"
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700"
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Pinned Footer buttons */}
        <div className="px-5 py-3 border-t border-border flex items-center justify-end gap-2 shrink-0 bg-surface">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-md bg-zinc-900 border border-zinc-800 text-zinc-300 hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            className="px-4 py-1.5 rounded-md bg-white text-zinc-950 font-semibold hover:bg-zinc-200 transition-colors shadow-sm"
          >
            Create Profile
          </button>
        </div>
      </form>
    </div>
  </div>
  );
};
