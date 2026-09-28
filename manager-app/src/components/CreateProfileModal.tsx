import React, { useEffect, useState } from 'react';
import { Cpu, Globe, Monitor, Shield, X } from 'lucide-react';
import { fetchProfileDefaults } from '../services/api';
import { ProfileCreateRequest, RequestedEnvironment, ResourceLimits } from '../types/profile';
import { formatProxyGeography, ProxyItem } from '../types/proxy';
import { RESOLUTION_OPTIONS, getHostTimezone } from '../services/fingerprintPool';

interface CreateProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (request: ProfileCreateRequest) => Promise<void>;
  existingCount: number;
  proxies?: ProxyItem[];
}

const fallbackEnvironment = (): RequestedEnvironment => ({
  screen_resolution: '1920x1080',
  timezone_policy: 'host',
  timezone: getHostTimezone(),
  language: 'en-US',
  user_agent_policy: 'browser_default',
  user_agent: null,
  rendering_mode: 'host_gpu',
});

export const CreateProfileModal: React.FC<CreateProfileModalProps> = ({
  isOpen, onClose, onCreate, existingCount, proxies = [],
}) => {
  const [name, setName] = useState('');
  const [environment, setEnvironment] = useState<RequestedEnvironment>(fallbackEnvironment);
  const [resources, setResources] = useState<ResourceLimits>({ cpu_limit: 4, memory_mb: 4096 });
  const [cpuOptions, setCpuOptions] = useState([1, 2, 4, 6, 8]);
  const [memoryOptions, setMemoryOptions] = useState([1024, 2048, 3072, 4096, 6144, 8192]);
  const [resolutions, setResolutions] = useState(RESOLUTION_OPTIONS.map((item) => item.value));
  const [networkChoice, setNetworkChoice] = useState('direct');
  const [customHost, setCustomHost] = useState('');
  const [customPort, setCustomPort] = useState('1080');
  const [customUser, setCustomUser] = useState('');
  const [customPassword, setCustomPassword] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setName(`Account ${existingCount + 1}`);
    setNetworkChoice('direct');
    setCustomHost('');
    setCustomPort('1080');
    setCustomUser('');
    setCustomPassword('');
    setError(null);
    fetchProfileDefaults()
      .then((defaults) => {
        setEnvironment(defaults.default_environment);
        setResources(defaults.default_resources);
        setCpuOptions(defaults.resource_options.cpu_limits.filter((value) => value <= defaults.resource_options.host_cpu_threads));
        setMemoryOptions(defaults.resource_options.memory_mb.filter((value) => value <= defaults.resource_options.host_memory_mb));
        setResolutions(defaults.supported_resolutions);
      })
      .catch(() => setEnvironment(fallbackEnvironment()));
  }, [isOpen, existingCount]);

  if (!isOpen) return null;

  const availableProxies = proxies.filter((proxy) => !proxy.assigned);
  const selectedProxy = availableProxies.find((proxy) => proxy.id === networkChoice);

  const handleNetworkChange = (choice: string) => {
    setNetworkChoice(choice);
    if (choice === 'direct') {
      setEnvironment((current) => ({
        ...current, timezone_policy: 'host', timezone: getHostTimezone(),
      }));
    } else if (choice === 'custom') {
      setEnvironment((current) => ({
        ...current, timezone_policy: 'manual', timezone: getHostTimezone(),
      }));
      setCustomHost('');
      setCustomPort('1080');
      setCustomUser('');
      setCustomPassword('');
    } else {
      const proxy = availableProxies.find((item) => item.id === choice);
      setEnvironment((current) => ({
        ...current, timezone_policy: 'proxy', timezone: proxy?.timezone || null,
      }));
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsSaving(true);
    try {
      const network: ProfileCreateRequest['network'] = networkChoice === 'direct'
        ? { mode: 'direct' }
        : networkChoice === 'custom'
          ? {
              mode: 'custom', host: customHost.trim(), port: Number(customPort),
              username: customUser.trim(), password: customPassword,
            }
          : { mode: 'pool', proxy_id: networkChoice };
      await onCreate({ name: name.trim(), network, requested_environment: environment, resources });
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create profile');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Create Isolated Profile</h2>
            <p className="mt-0.5 text-[11px] text-zinc-500">Identity and ports are allocated safely by the manager.</p>
          </div>
          <button type="button" onClick={onClose} className="text-zinc-400 hover:text-white"><X className="h-4 w-4" /></button>
        </div>

        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto p-5 text-xs">
            <label className="block text-zinc-400">
              <span className="mb-1.5 block font-medium">Profile name</span>
              <input required value={name} onChange={(e) => setName(e.target.value)}
                className="w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100 focus:border-zinc-600 focus:outline-none" />
            </label>

            <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
              <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Globe className="h-3.5 w-3.5" />Network</div>
              <select value={networkChoice} onChange={(e) => handleNetworkChange(e.target.value)}
                className="w-full rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100">
                <option value="direct">Direct connection</option>
                {availableProxies.map((proxy) => (
                  <option key={proxy.id} value={proxy.id}>{proxy.host}:{proxy.port} — {formatProxyGeography(proxy)}</option>
                ))}
                <option value="custom">Custom proxy</option>
              </select>
              {selectedProxy && (
                <p className="text-[11px] text-zinc-400">{formatProxyGeography(selectedProxy)}</p>
              )}
              {selectedProxy && !selectedProxy.timezone && (
                <label className="block text-amber-300">Timezone required because this proxy has no location metadata
                  <input required value={environment.timezone || ''}
                    onChange={(e) => setEnvironment((current) => ({ ...current, timezone: e.target.value }))}
                    placeholder="IANA timezone, e.g. America/Guatemala"
                    className="mt-1 w-full rounded border border-amber-800/60 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
                </label>
              )}
              {networkChoice === 'custom' && (
                <div className="grid grid-cols-3 gap-2">
                  <input required value={customHost} onChange={(e) => setCustomHost(e.target.value)} placeholder="Proxy host"
                    className="col-span-2 rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
                  <input required type="number" min={1} max={65535} value={customPort} onChange={(e) => setCustomPort(e.target.value)}
                    className="rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
                  <input value={customUser} onChange={(e) => setCustomUser(e.target.value)} placeholder="Username"
                    className="rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
                  <input type="password" value={customPassword} onChange={(e) => setCustomPassword(e.target.value)} placeholder="Password"
                    className="rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
                  <input required value={environment.timezone || ''}
                    onChange={(e) => setEnvironment((current) => ({ ...current, timezone: e.target.value }))}
                    placeholder="IANA timezone" className="rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
                </div>
              )}
            </div>

            <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
              <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Cpu className="h-3.5 w-3.5" />Container resource limits</div>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-zinc-500">CPU quota
                  <select value={resources.cpu_limit} onChange={(e) => setResources((current) => ({ ...current, cpu_limit: Number(e.target.value) }))}
                    className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200">
                    {cpuOptions.map((value) => <option key={value} value={value}>{value} vCPU</option>)}
                  </select>
                </label>
                <label className="text-zinc-500">Memory ceiling
                  <select value={resources.memory_mb} onChange={(e) => setResources((current) => ({ ...current, memory_mb: Number(e.target.value) }))}
                    className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200">
                    {memoryOptions.map((value) => <option key={value} value={value}>{value / 1024} GiB</option>)}
                  </select>
                </label>
              </div>
              <p className="text-[10px] leading-relaxed text-zinc-500">Limits are maximums, not reserved resources. Default: 4 vCPU and 4 GiB.</p>
            </div>

            <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
              <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Monitor className="h-3.5 w-3.5" />Browser environment</div>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-zinc-500">Display resolution
                  <select value={environment.screen_resolution}
                    onChange={(e) => setEnvironment((current) => ({ ...current, screen_resolution: e.target.value }))}
                    className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200">
                    {resolutions.map((resolution) => <option key={resolution}>{resolution}</option>)}
                  </select>
                </label>
                <label className="text-zinc-500">Language
                  <input value={environment.language}
                    onChange={(e) => setEnvironment((current) => ({ ...current, language: e.target.value }))}
                    className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200" />
                </label>
                <label className="text-zinc-500">User agent
                  <div className="mt-1 rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-300">Browser managed</div>
                </label>
                <label className="text-zinc-500">Rendering
                  <select value={environment.rendering_mode}
                    onChange={(e) => setEnvironment((current) => ({ ...current, rendering_mode: e.target.value as RequestedEnvironment['rendering_mode'] }))}
                    className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200">
                    <option value="host_gpu">Host GPU</option><option value="software">Software rendering</option>
                  </select>
                </label>
              </div>
              <p className="flex gap-1.5 text-[10px] leading-relaxed text-zinc-500">
                <Shield className="mt-0.5 h-3 w-3 shrink-0" />Only settings applied at container startup are configurable here. Runtime measurements appear separately in the inspector.
              </p>
            </div>
            {error && <p className="rounded border border-red-900/50 bg-red-950/30 p-2 text-red-300">{error}</p>}
          </div>
          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <button type="button" onClick={onClose} className="rounded border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300">Cancel</button>
            <button disabled={isSaving} type="submit" className="rounded bg-white px-4 py-1.5 font-semibold text-zinc-950 disabled:opacity-50">
              {isSaving ? 'Creating…' : 'Create profile'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
