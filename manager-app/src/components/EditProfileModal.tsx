import { ProfileGroupField } from './ProfileGroupField';
import React, { useEffect, useState } from 'react';
import { AlertTriangle, ChevronDown, Cpu, Globe, Monitor, X } from 'lucide-react';
import { BehaviorMode, Profile, ProfileUpdateRequest, ReelTemplateSelection, RequestedEnvironment, ResourceLimits, reelTemplateLabel } from '../types/profile';
import { formatProxyGeography, ProxyItem } from '../types/proxy';
import { SCREEN_RESOLUTIONS, getHostTimezone } from '../services/fingerprintPool';
import { fetchProfileDefaults } from '../services/api';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';
import { useTimezoneConfirmation } from './TimezoneConfirmationDialog';

interface EditProfileModalProps {
  profile: Profile | null;
  isOpen: boolean;
  onClose: () => void;
  onSave: (profileId: string, updates: ProfileUpdateRequest) => Promise<void>;
  proxies?: ProxyItem[];
  groups?: string[];
}

function environmentFor(profile: Profile): RequestedEnvironment {
  return profile.requested_environment || {
    screen_resolution: profile.fingerprint.screen_resolution,
    timezone_policy: profile.network.proxy_host ? 'proxy' : 'host',
    timezone: profile.fingerprint.timezone,
    language: profile.fingerprint.language,
    user_agent_policy: 'browser_default',
    user_agent: null,
    rendering_mode: 'host_gpu',
  };
}

export const EditProfileModal: React.FC<EditProfileModalProps> = ({
  profile, isOpen, onClose, onSave, proxies = [], groups = [],
}) => {
  const { confirmTimezone, timezoneDialog } = useTimezoneConfirmation();
  const [name, setName] = useState('');
  const [group, setGroup] = useState('');
  const [environment, setEnvironment] = useState<RequestedEnvironment | null>(null);
  const [resources, setResources] = useState<ResourceLimits>({ cpu_limit: 4, memory_mb: 4096 });
  const [behaviorMode, setBehaviorMode] = useState<BehaviorMode>('medium');
  const [reelTemplate, setReelTemplate] = useState<ReelTemplateSelection>('auto');
  const [behaviorOptions, setBehaviorOptions] = useState<BehaviorMode[]>(['fast', 'medium', 'slow']);
  const [reelTemplateOptions, setReelTemplateOptions] = useState<ReelTemplateSelection[]>(['auto', 't1', 't2', 't3']);
  const [cpuOptions, setCpuOptions] = useState([1, 2, 4, 6, 8]);
  const [memoryOptions, setMemoryOptions] = useState([1024, 2048, 3072, 4096, 6144, 8192]);
  const [networkChoice, setNetworkChoice] = useState('direct');
  const [customHost, setCustomHost] = useState('');
  const [customPort, setCustomPort] = useState('1080');
  const [customUser, setCustomUser] = useState('');
  const [customPassword, setCustomPassword] = useState('');
  const [warmingWeek, setWarmingWeek] = useState(1);
  const [warmingComplete, setWarmingComplete] = useState(false);
  const [notes, setNotes] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !profile) return;
    setAdvancedOpen(false);
    setName(profile.name);
    setGroup(profile.group || '');
    setEnvironment(environmentFor(profile));
    setResources(profile.resources || { cpu_limit: 4, memory_mb: 4096 });
    setBehaviorMode(profile.behavior_mode || 'medium');
    setReelTemplate(profile.automation?.reel_template || 'auto');
    setWarmingWeek(profile.account.warming_week || 1);
    setWarmingComplete(Boolean(profile.account.warming_complete));
    setNotes(profile.account.notes || '');
    setError(null);
    if (profile.network.mode === 'pool' && profile.network.proxy_id) {
      setNetworkChoice(profile.network.proxy_id);
    } else if (profile.network.proxy_host) {
      setNetworkChoice('custom');
    } else {
      setNetworkChoice('direct');
    }
    setCustomHost(profile.network.proxy_host || '');
    setCustomPort(String(profile.network.proxy_port || 1080));
    setCustomUser(profile.network.proxy_user || '');
    setCustomPassword(profile.network.proxy_pass || '');
    fetchProfileDefaults().then((defaults) => {
      setCpuOptions(defaults.resource_options.cpu_limits.filter((value) => value <= defaults.resource_options.host_cpu_threads));
      setMemoryOptions(defaults.resource_options.memory_mb.filter((value) => value <= defaults.resource_options.host_memory_mb));
      setBehaviorOptions(defaults.behavior_modes);
      setReelTemplateOptions(defaults.reel_template_options);
    }).catch(() => undefined);
  }, [isOpen, profile?.id]);

  if (!isOpen || !profile || !environment) return null;

  const poolChoices = proxies.filter(
    (proxy) => !proxy.assigned || proxy.profile_id === profile.id || proxy.id === profile.network.proxy_id
  );
  const selectedPoolProxy = poolChoices.find((proxy) => proxy.id === networkChoice);

  const changeNetwork = (choice: string) => {
    setNetworkChoice(choice);
    if (choice === 'direct') {
      setEnvironment((current) => current && ({ ...current, timezone_policy: 'host', timezone: getHostTimezone() }));
    } else if (choice === 'custom') {
      setCustomHost('');
      setCustomPort('1080');
      setCustomUser('');
      setCustomPassword('');
      setEnvironment((current) => current && ({ ...current, timezone_policy: 'proxy', timezone: null }));
    } else {
      const proxy = poolChoices.find((item) => item.id === choice);
      setEnvironment((current) => current && ({ ...current, timezone_policy: 'proxy', timezone: proxy?.timezone || null }));
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const network: ProfileUpdateRequest['network'] = networkChoice === 'direct'
        ? { mode: 'direct' }
        : networkChoice === 'custom'
          ? { mode: 'custom', host: customHost.trim(), port: Number(customPort), username: customUser.trim(), password: customPassword }
          : { mode: 'pool', proxy_id: networkChoice };
      const confirmedEnvironment = await confirmTimezone(environment, network);
      if (!confirmedEnvironment) return;
      setEnvironment(confirmedEnvironment);
      await onSave(profile.id, {
        name: name.trim(), group: group.trim(), network, requested_environment: confirmedEnvironment, resources,
        behavior_mode: behaviorMode,
        automation: { reel_template: reelTemplate },
        account: { warming_week: warmingWeek, warming_complete: warmingComplete, notes: notes.trim() },
      });
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save profile');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <>
    {timezoneDialog}
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div className="max-h-[90vh] w-full max-w-lg overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div><h2 className="text-sm font-semibold text-zinc-100">Edit profile</h2><p className="font-mono text-[11px] text-zinc-500">{profile.id}</p></div>
          <button type="button" onClick={onClose} className="text-zinc-400 hover:text-white"><X className="h-4 w-4" /></button>
        </div>
        <form onSubmit={submit} className="max-h-[80vh] space-y-4 overflow-y-auto p-5 text-xs">
          {profile.status === 'running' && (
            <div className="flex gap-2 rounded border border-amber-800/50 bg-amber-950/20 p-2 text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />Runtime changes are saved as requested settings and require a restart before they become effective.
            </div>
          )}
          <label className="block text-zinc-400">Profile name
            <input required value={name} onChange={(e) => setName(e.target.value)} className="mt-1.5 w-full rounded border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100" />
          </label>
          <ProfileGroupField value={group} onChange={setGroup} groups={groups} />
          <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
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
            <p className="text-[10px] leading-relaxed text-zinc-500">Auto uses two-stage Reel detection. A selected template bypasses selection and validates its expected screens. Changes apply to subsequent jobs.</p>
          </div>
          <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
            <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Globe className="h-3.5 w-3.5" />Network</div>
            <Select value={networkChoice} onValueChange={changeNetwork} ariaLabel="Network connection"
              options={[
                { value: 'direct', label: 'Direct connection' },
                ...poolChoices.map((proxy) => ({ value: proxy.id, label: `${proxy.host}:${proxy.port} — ${formatProxyGeography(proxy)}` })),
                { value: 'custom', label: 'Custom proxy' },
              ]} />
            {selectedPoolProxy && <p className="text-[11px] text-zinc-400">{formatProxyGeography(selectedPoolProxy)}</p>}
            {networkChoice === 'custom' && (
              <div className="grid grid-cols-3 gap-2">
                <input required value={customHost} onChange={(e) => setCustomHost(e.target.value)} placeholder="Proxy host" className="col-span-2 rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200" />
                <input required type="number" min={1} max={65535} value={customPort} onChange={(e) => setCustomPort(e.target.value)} className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200" />
                <input value={customUser} onChange={(e) => setCustomUser(e.target.value)} placeholder="Username" className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200" />
                <input type="password" value={customPassword} onChange={(e) => setCustomPassword(e.target.value)} placeholder="Password" className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200" />
              </div>
            )}
          </div>
          {networkChoice === 'direct' && environment.timezone_policy === 'manual' && (
            <label className="block text-xs text-zinc-400">Confirmed timezone
              <input required value={environment.timezone || ''}
                onChange={(event) => setEnvironment({ ...environment, timezone: event.target.value })}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-zinc-100" />
            </label>
          )}
          <div className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950">
            <button type="button" onClick={() => setAdvancedOpen((open) => !open)}
              aria-expanded={advancedOpen} aria-controls="edit-profile-advanced"
              className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left text-zinc-300 hover:bg-zinc-900 disabled:opacity-50">
              <span>
                <span className="block font-medium">Advanced settings</span>
                <span className="mt-1 block text-[11px] text-zinc-500">Resources and browser environment</span>
              </span>
              <ChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
            </button>
            <div id="edit-profile-advanced" hidden={!advancedOpen} className="space-y-3 border-t border-zinc-800 p-3">
              <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
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
                <p className="text-[10px] text-zinc-500">These are ceilings, not reserved CPU or memory. Changes apply after restart.</p>
              </div>
              <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
                <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Monitor className="h-3.5 w-3.5" />Requested browser environment</div>
                <div className="grid grid-cols-2 gap-2">
                  <Select value={environment.screen_resolution} onValueChange={(value) => setEnvironment({ ...environment, screen_resolution: value })}
                    ariaLabel="Display resolution" className="min-h-8 py-1.5"
                    options={SCREEN_RESOLUTIONS.map((resolution) => ({ value: resolution, label: resolution }))} />
                  <input value={environment.language} onChange={(e) => setEnvironment({ ...environment, language: e.target.value })} className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-200" />
                  <div className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-zinc-300">Browser-managed UA</div>
                  <Select value={environment.rendering_mode} onValueChange={(value) => setEnvironment({ ...environment, rendering_mode: value as RequestedEnvironment['rendering_mode'] })}
                    ariaLabel="Rendering mode" className="min-h-8 py-1.5"
                    options={[{ value: 'host_gpu', label: 'Host GPU' }, { value: 'software', label: 'Software renderer' }]} />
                </div>
              </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-zinc-400">Warming week<input type="number" min={1} value={warmingWeek} onChange={(e) => setWarmingWeek(Number(e.target.value))} className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-zinc-200" /></label>
            <label htmlFor="warming-complete" className="flex cursor-pointer items-end gap-2 pb-2 text-zinc-400">
              <Checkbox id="warming-complete" checked={warmingComplete} onCheckedChange={setWarmingComplete} />Warming complete
            </label>
          </div>
          <label className="block text-zinc-400">Notes<textarea value={notes} onChange={(e) => setNotes(e.target.value)} className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-zinc-200" /></label>
          {error && <p className="rounded border border-red-900/50 bg-red-950/30 p-2 text-red-300">{error}</p>}
          <div className="flex justify-end gap-2 border-t border-border pt-3">
            <button type="button" onClick={onClose} className="rounded border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300">Cancel</button>
            <button disabled={isSaving} type="submit" className="rounded bg-white px-4 py-1.5 font-semibold text-zinc-950 disabled:opacity-50">{isSaving ? 'Saving…' : 'Save changes'}</button>
          </div>
        </form>
      </div>
    </div>
    </>
  );
};
