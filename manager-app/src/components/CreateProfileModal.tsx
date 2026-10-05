import { ProfileGroupField } from './ProfileGroupField';
import React, { useEffect, useState } from 'react';
import { Cpu, Globe, Loader2, Monitor, Shield, X } from 'lucide-react';
import { fetchProfileDefaults } from '../services/api';
import { BehaviorMode, ProfileCreateRequest, ReelTemplateSelection, RequestedEnvironment, ResourceLimits, reelTemplateLabel } from '../types/profile';
import { formatProxyGeography, ProxyItem } from '../types/proxy';
import { RESOLUTION_OPTIONS, getHostTimezone } from '../services/fingerprintPool';
import { Select } from './ui/Select';
import { useTimezoneConfirmation } from './TimezoneConfirmationDialog';

interface CreateProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (request: ProfileCreateRequest) => Promise<void>;
  existingCount: number;
  proxies?: ProxyItem[];
  groups?: string[];
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
  isOpen, onClose, onCreate, existingCount, proxies = [], groups = [],
}) => {
  const { confirmTimezone, timezoneDialog, awaitingTimezoneConfirmation } = useTimezoneConfirmation();
  const [name, setName] = useState('');
  const [group, setGroup] = useState('');
  const [environment, setEnvironment] = useState<RequestedEnvironment>(fallbackEnvironment);
  const [resources, setResources] = useState<ResourceLimits>({ cpu_limit: 4, memory_mb: 4096 });
  const [behaviorMode, setBehaviorMode] = useState<BehaviorMode>('medium');
  const [reelTemplate, setReelTemplate] = useState<ReelTemplateSelection>('auto');
  const [behaviorOptions, setBehaviorOptions] = useState<BehaviorMode[]>(['fast', 'medium', 'slow']);
  const [reelTemplateOptions, setReelTemplateOptions] = useState<ReelTemplateSelection[]>(['auto', 't1', 't2', 't3']);
  const [cpuOptions, setCpuOptions] = useState([1, 2, 4, 6, 8]);
  const [memoryOptions, setMemoryOptions] = useState([1024, 2048, 3072, 4096, 6144, 8192]);
  const [resolutions, setResolutions] = useState(RESOLUTION_OPTIONS.map((item) => item.value));
  const [networkChoice, setNetworkChoice] = useState('direct');
  const [customHost, setCustomHost] = useState('');
  const [customPort, setCustomPort] = useState('1080');
  const [customUser, setCustomUser] = useState('');
  const [customPassword, setCustomPassword] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [creationStage, setCreationStage] = useState<'timezone' | 'saving'>('timezone');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setName(`Account ${existingCount + 1}`);
    setGroup('');
    setNetworkChoice('direct');
    setCustomHost('');
    setCustomPort('1080');
    setCustomUser('');
    setCustomPassword('');
    setBehaviorMode('medium');
    setReelTemplate('auto');
    setError(null);
    fetchProfileDefaults()
      .then((defaults) => {
        setEnvironment(defaults.default_environment);
        setResources(defaults.default_resources);
        setBehaviorMode(defaults.default_behavior_mode);
        setReelTemplate(defaults.default_automation.reel_template);
        setBehaviorOptions(defaults.behavior_modes);
        setReelTemplateOptions(defaults.reel_template_options);
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
        ...current, timezone_policy: 'proxy', timezone: null,
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
    if (isSaving) return;
    setError(null);
    setCreationStage('timezone');
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
      const confirmedEnvironment = await confirmTimezone(environment, network);
      if (!confirmedEnvironment) return;
      setEnvironment(confirmedEnvironment);
      setCreationStage('saving');
      await onCreate({
        name: name.trim(), group: group.trim(), network, requested_environment: confirmedEnvironment, resources,
        behavior_mode: behaviorMode,
        automation: { reel_template: reelTemplate },
      });
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create profile');
    } finally {
      setIsSaving(false);
    }
  };

  const progressMessage = awaitingTimezoneConfirmation
    ? 'Waiting for timezone confirmation…'
    : creationStage === 'timezone'
      ? networkChoice === 'direct' ? 'Detecting your computer’s timezone…' : 'Detecting timezone from the proxy’s exit IP…'
      : networkChoice === 'direct' ? 'Creating profile…' : 'Checking the proxy and creating profile…';

  return (
    <>
    {timezoneDialog}
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Create Isolated Profile</h2>
            <p className="mt-0.5 text-[11px] text-zinc-500">Identity and ports are allocated safely by the manager.</p>
          </div>
          <button type="button" disabled={isSaving} onClick={onClose} aria-label="Close create profile" className="text-zinc-400 hover:text-white disabled:opacity-40"><X className="h-4 w-4" /></button>
        </div>

        <form onSubmit={submit} aria-busy={isSaving && !awaitingTimezoneConfirmation} className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto p-5 text-xs">
            <fieldset disabled={isSaving} className="contents space-y-4">
            <label className="block text-zinc-400">
              <span className="mb-1.5 block font-medium">Profile name</span>
              <input required value={name} onChange={(e) => setName(e.target.value)}
                className="w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100 focus:border-zinc-600 focus:outline-none" />
            </label>

            <ProfileGroupField value={group} onChange={setGroup} groups={groups} />
          {networkChoice === 'direct' && environment.timezone_policy === 'manual' && (
            <label className="block text-xs text-zinc-400">Confirmed timezone
              <input required value={environment.timezone || ''}
                onChange={(event) => setEnvironment({ ...environment, timezone: event.target.value })}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
            </label>
          )}
            <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
              <div className="font-medium text-zinc-300">Automation behavior</div>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-zinc-500">Behavior mode
                  <Select value={behaviorMode} onValueChange={(value) => setBehaviorMode(value as BehaviorMode)}
                    ariaLabel="Behavior mode" className="mt-1 min-h-8 py-1.5"
                    options={behaviorOptions.map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }))} />
                </label>
                <label className="text-zinc-500">Reel template
                  <Select value={reelTemplate} onValueChange={(value) => setReelTemplate(value as ReelTemplateSelection)}
                    ariaLabel="Reel template" className="mt-1 min-h-8 py-1.5"
                    options={reelTemplateOptions.map((value) => ({ value, label: reelTemplateLabel(value) }))} />
                </label>
              </div>
              <p className="text-[10px] leading-relaxed text-zinc-500">Auto first detects Studio or Direct upload, then distinguishes Direct from Next / Share. A selected template bypasses selection and validates its expected screens.</p>
            </div>

            <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
              <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Globe className="h-3.5 w-3.5" />Network</div>
              <Select value={networkChoice} onValueChange={handleNetworkChange}
                ariaLabel="Network connection"
                options={[
                  { value: 'direct', label: 'Direct connection' },
                  ...availableProxies.map((proxy) => ({ value: proxy.id, label: `${proxy.host}:${proxy.port} — ${formatProxyGeography(proxy)}` })),
                  { value: 'custom', label: 'Custom proxy' },
                ]} />
              {selectedProxy && (
                <p className="text-[11px] text-zinc-400">{formatProxyGeography(selectedProxy)}</p>
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
                </div>
              )}
            </div>

            <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
              <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Cpu className="h-3.5 w-3.5" />Container resource limits</div>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-zinc-500">CPU quota
                  <Select value={String(resources.cpu_limit)} onValueChange={(value) => setResources((current) => ({ ...current, cpu_limit: Number(value) }))}
                    ariaLabel="CPU quota" className="mt-1 min-h-8 py-1.5"
                    options={cpuOptions.map((value) => ({ value: String(value), label: `${value} vCPU` }))} />
                </label>
                <label className="text-zinc-500">Memory ceiling
                  <Select value={String(resources.memory_mb)} onValueChange={(value) => setResources((current) => ({ ...current, memory_mb: Number(value) }))}
                    ariaLabel="Memory ceiling" className="mt-1 min-h-8 py-1.5"
                    options={memoryOptions.map((value) => ({ value: String(value), label: `${value / 1024} GiB` }))} />
                </label>
              </div>
              <p className="text-[10px] leading-relaxed text-zinc-500">Limits are maximums, not reserved resources. Default: 4 vCPU and 4 GiB.</p>
            </div>

            <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
              <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Monitor className="h-3.5 w-3.5" />Browser environment</div>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-zinc-500">Display resolution
                  <Select value={environment.screen_resolution}
                    onValueChange={(value) => setEnvironment((current) => ({ ...current, screen_resolution: value }))}
                    ariaLabel="Display resolution" className="mt-1 min-h-8 py-1.5"
                    options={resolutions.map((resolution) => ({ value: resolution, label: resolution }))} />
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
                  <Select value={environment.rendering_mode}
                    onValueChange={(value) => setEnvironment((current) => ({ ...current, rendering_mode: value as RequestedEnvironment['rendering_mode'] }))}
                    ariaLabel="Rendering mode" className="mt-1 min-h-8 py-1.5"
                    options={[{ value: 'host_gpu', label: 'Host GPU' }, { value: 'software', label: 'Software rendering' }]} />
                </label>
              </div>
              <p className="flex gap-1.5 text-[10px] leading-relaxed text-zinc-500">
                <Shield className="mt-0.5 h-3 w-3 shrink-0" />Only settings applied at container startup are configurable here. Runtime measurements appear separately in the inspector.
              </p>
            </div>
            {error && <p className="rounded border border-red-900/50 bg-red-950/30 p-2 text-red-300">{error}</p>}
            </fieldset>
          </div>
          <div className="space-y-3 border-t border-border px-5 py-3">
            {isSaving && (
              <div role="status" aria-live="polite" className="flex items-start gap-2 rounded-lg border border-blue-900/60 bg-blue-950/30 p-3 text-xs text-blue-200">
                {!awaitingTimezoneConfirmation && <Loader2 aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />}
                <div>
                  <p className="font-medium">{progressMessage}</p>
                  {!awaitingTimezoneConfirmation && <p className="mt-1 text-[11px] text-blue-200/70">{networkChoice === 'direct' ? 'Please wait while the profile is prepared.' : 'Please wait. Proxy checks may take a few moments.'}</p>}
                </div>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button type="button" disabled={isSaving} onClick={onClose} className="rounded border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300 disabled:opacity-40">Cancel</button>
              <button disabled={isSaving} type="submit" className="flex items-center gap-2 rounded bg-white px-4 py-1.5 font-semibold text-zinc-950 disabled:opacity-50">
                {isSaving && !awaitingTimezoneConfirmation && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
                {isSaving ? awaitingTimezoneConfirmation ? 'Confirm timezone…' : creationStage === 'timezone' ? 'Checking timezone…' : 'Creating…' : 'Create profile'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
    </>
  );
};
