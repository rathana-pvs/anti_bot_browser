import React, { useEffect, useState } from 'react';
import { AlertTriangle, Check, ChevronDown } from 'lucide-react';
import { BehaviorMode, Profile, ProfileUpdateRequest, ReelTemplateSelection, RequestedEnvironment, ResourceLimits, reelTemplateLabel } from '../../types/profile';
import { formatProxyGeography, ProxyItem } from '../../types/proxy';
import { SCREEN_RESOLUTIONS, getHostTimezone } from '../../services/fingerprintPool';
import { fetchProfileDefaults } from '../../services/api';
import { fetchLiveSettings, saveLiveSettings, LiveSettings } from '../../services/live';
import { ProfileGroupField } from '../ProfileGroupField';
import { ProxySpeedTest } from '../ProxySpeedTest';
import { useTimezoneConfirmation } from '../TimezoneConfirmationDialog';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Select } from '../ui/Select';

const DEFAULT_LIVE: LiveSettings = { server_url: 'rtmps://live-api-s.facebook.com:443/rtmp/', producer_url: 'https://www.facebook.com/live/producer/', key_saved: false };
const input = 'mt-1.5 w-full rounded-control border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100 outline-none focus:border-blue-500';

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

function Section({ id, title, description, children }: { id: string; title: string; description?: string; children: React.ReactNode }) {
  return <section id={id} aria-labelledby={`${id}-title`} className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
    <div>
      <h3 id={`${id}-title`} className="text-sm font-semibold text-zinc-100">{title}</h3>
      {description && <p className="mt-0.5 text-[11px] text-zinc-500">{description}</p>}
    </div>
    {children}
  </section>;
}

/** Every editable account setting on one page, saved together. */
export function ProfileSettings({ profile, onSave, proxies = [], groups = [] }: {
  profile: Profile; onSave: (profileId: string, updates: ProfileUpdateRequest) => Promise<void>;
  proxies?: ProxyItem[]; groups?: string[];
}) {
  const { confirmTimezone, timezoneDialog } = useTimezoneConfirmation();
  const [resetKey, setResetKey] = useState(0);
  const [liveSettings, setLiveSettings] = useState<LiveSettings>(DEFAULT_LIVE);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveKey, setLiveKey] = useState('');
  const [liveDirty, setLiveDirty] = useState(false);
  const [liveError, setLiveError] = useState('');
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
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load once per account (or on Discard). Background profile refreshes must not overwrite edits in progress.
  useEffect(() => {
    let disposed = false;
    setLiveSettings(DEFAULT_LIVE);
    setLiveLoading(true); setLiveKey(''); setLiveDirty(false); setLiveError('');
    fetchLiveSettings(profile.id).then(value => { if (!disposed) setLiveSettings(value); })
      .catch(reason => { if (!disposed) setLiveError(reason.message || 'Couldn’t load Live settings'); })
      .finally(() => { if (!disposed) setLiveLoading(false); });
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
    setSaved(false);
    if (profile.network.mode === 'pool' && profile.network.proxy_id) setNetworkChoice(profile.network.proxy_id);
    else if (profile.network.proxy_host) setNetworkChoice('custom');
    else setNetworkChoice('direct');
    setCustomHost(profile.network.proxy_host || '');
    setCustomPort(String(profile.network.proxy_port || 1080));
    setCustomUser(profile.network.proxy_user || '');
    setCustomPassword(profile.network.proxy_pass || '');
    fetchProfileDefaults().then((defaults) => {
      if (disposed) return;
      setCpuOptions(defaults.resource_options.cpu_limits.filter((value) => value <= defaults.resource_options.host_cpu_threads));
      setMemoryOptions(defaults.resource_options.memory_mb.filter((value) => value <= defaults.resource_options.host_memory_mb));
      setBehaviorOptions(defaults.behavior_modes);
      setReelTemplateOptions(defaults.reel_template_options);
    }).catch(() => undefined);
    return () => { disposed = true; };
    // Re-reading the profile on every poll would discard unsaved edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id, resetKey]);

  if (!environment) return null;

  const poolChoices = proxies.filter((proxy) => !proxy.assigned || proxy.profile_id === profile.id || proxy.id === profile.network.proxy_id);
  const selectedPoolProxy = poolChoices.find((proxy) => proxy.id === networkChoice);
  const originalNetworkChoice = profile.network.mode === 'pool' && profile.network.proxy_id
    ? profile.network.proxy_id : profile.network.proxy_host ? 'custom' : 'direct';
  const originalEnvironment = environmentFor(profile);
  const browserSettingsChanged = Object.keys(originalEnvironment).some(key =>
    environment[key as keyof RequestedEnvironment] !== originalEnvironment[key as keyof RequestedEnvironment]);
  const networkChanged = networkChoice !== originalNetworkChoice || (networkChoice === 'custom' && (
    customHost.trim() !== (profile.network.proxy_host || '') || Number(customPort) !== (profile.network.proxy_port || 1080) ||
    customUser.trim() !== (profile.network.proxy_user || '') || customPassword !== (profile.network.proxy_pass || '')));
  const resourcesChanged = resources.cpu_limit !== (profile.resources?.cpu_limit ?? 4) || resources.memory_mb !== (profile.resources?.memory_mb ?? 4096);
  const needsBrowserRestart = profile.status === 'running' && (browserSettingsChanged || networkChanged || resourcesChanged);

  const changeNetwork = (choice: string) => {
    setNetworkChoice(choice);
    if (choice === 'direct') {
      setEnvironment((current) => current && ({ ...current, timezone_policy: 'host', timezone: getHostTimezone() }));
    } else if (choice === 'custom') {
      setCustomHost(''); setCustomPort('1080'); setCustomUser(''); setCustomPassword('');
      setEnvironment((current) => current && ({ ...current, timezone_policy: 'proxy', timezone: null }));
    } else {
      const proxy = poolChoices.find((item) => item.id === choice);
      setEnvironment((current) => current && ({ ...current, timezone_policy: 'proxy', timezone: proxy?.timezone || null }));
    }
  };

  const speedTestNetwork = networkChoice === 'custom'
    ? { mode: 'custom' as const, host: customHost.trim(), port: Number(customPort), username: customUser.trim(), password: customPassword }
    : networkChoice === 'direct' ? { mode: 'direct' as const } : { mode: 'pool' as const, proxy_id: networkChoice };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    setSaved(false);
    try {
      if (liveDirty && (liveError || liveLoading)) throw new Error('Live settings couldn’t be loaded. Retry the connection before changing the key or URL.');
      const network: ProfileUpdateRequest['network'] = networkChoice === 'direct'
        ? { mode: 'direct' }
        : networkChoice === 'custom'
          ? { mode: 'custom', host: customHost.trim(), port: Number(customPort), username: customUser.trim(), password: customPassword }
          : { mode: 'pool', proxy_id: networkChoice };
      const confirmedEnvironment = await confirmTimezone(environment, network);
      if (!confirmedEnvironment) return;
      setEnvironment(confirmedEnvironment);
      if (liveDirty) {
        const savedLive = await saveLiveSettings(profile.id, { server_url: liveSettings.server_url, producer_url: liveSettings.producer_url, stream_key: liveKey });
        setLiveSettings(savedLive); setLiveKey(''); setLiveDirty(false);
      }
      await onSave(profile.id, {
        name: name.trim(), group: group.trim(), network, requested_environment: confirmedEnvironment, resources,
        behavior_mode: behaviorMode,
        automation: { reel_template: reelTemplate },
        account: { warming_week: warmingWeek, warming_complete: warmingComplete, notes: notes.trim() },
      });
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Couldn’t save this account.');
    } finally {
      setIsSaving(false);
    }
  };

  return <>
    {timezoneDialog}
    <form onSubmit={submit} className="mx-auto max-w-3xl space-y-4 text-xs" onChange={() => setSaved(false)}>
      {needsBrowserRestart && <div className="flex gap-2 rounded-xl border border-amber-800/50 bg-amber-950/20 p-3 text-amber-300">
        <AlertTriangle className="h-4 w-4 shrink-0" />Network, browser, and resource changes apply after this account’s browser restarts. Live keys apply to the next broadcast.
      </div>}

      <Section id="settings-general" title="General">
        <label className="block text-zinc-400">Name<input required value={name} onChange={(e) => setName(e.target.value)} className={input} /></label>
        <ProfileGroupField value={group} onChange={setGroup} groups={groups} />
        <div className="grid grid-cols-2 gap-3">
          <label className="text-zinc-400">Warm-up week<input type="number" min={1} value={warmingWeek} onChange={(e) => setWarmingWeek(Number(e.target.value))} className={input} /></label>
          <label htmlFor="warming-complete" className="flex cursor-pointer items-end gap-2 pb-2 text-zinc-400">
            <Checkbox id="warming-complete" checked={warmingComplete} onCheckedChange={(value) => { setWarmingComplete(value); setSaved(false); }} />Warm-up complete
          </label>
        </div>
        <label className="block text-zinc-400">Notes<textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} className={input} /></label>
      </Section>

      <Section id="settings-network" title="Network" description="How this account’s browser connects to the internet.">
        <Select value={networkChoice} onValueChange={(value) => { changeNetwork(value); setSaved(false); }} ariaLabel="Network connection"
          options={[
            { value: 'direct', label: 'Direct connection' },
            ...poolChoices.map((proxy) => ({ value: proxy.id, label: `${proxy.host}:${proxy.port} — ${formatProxyGeography(proxy)}` })),
            { value: 'custom', label: 'Custom proxy' },
          ]} />
        {selectedPoolProxy && <p className="text-[11px] text-zinc-400">{formatProxyGeography(selectedPoolProxy)}</p>}
        {networkChoice === 'custom' && <div className="grid grid-cols-3 gap-2">
          <input required value={customHost} onChange={(e) => setCustomHost(e.target.value)} placeholder="Proxy host" aria-label="Proxy host" className={`${input} col-span-2 mt-0`} />
          <input required type="number" min={1} max={65535} value={customPort} onChange={(e) => setCustomPort(e.target.value)} aria-label="Proxy port" className={`${input} mt-0`} />
          <input value={customUser} onChange={(e) => setCustomUser(e.target.value)} placeholder="Username" aria-label="Proxy username" className={`${input} mt-0`} />
          <input type="password" value={customPassword} onChange={(e) => setCustomPassword(e.target.value)} placeholder="Password" aria-label="Proxy password" className={`${input} mt-0`} />
        </div>}
        {networkChoice === 'direct' && environment.timezone_policy === 'manual' && <label className="block text-zinc-400">Confirmed timezone
          <input required value={environment.timezone || ''} onChange={(event) => setEnvironment({ ...environment, timezone: event.target.value })} className={input} />
        </label>}
        <ProxySpeedTest key={JSON.stringify(speedTestNetwork)} network={speedTestNetwork} disabled={isSaving} />
      </Section>

      <Section id="settings-live" title="Facebook Live" description="Saved once per account, then used by Live campaigns. Set up the destination in Facebook Live Producer before running.">
        {liveError && <div className="flex flex-wrap items-center gap-2"><p role="alert" className="text-amber-300">{liveError}</p>
          <Button type="button" size="sm" variant="outline" disabled={liveLoading} onClick={async () => {
            setLiveLoading(true);
            try {
              const loaded = await fetchLiveSettings(profile.id);
              setLiveSettings(current => liveDirty ? { ...current, key_saved: loaded.key_saved } : loaded);
              setLiveError('');
            } catch (reason) { setLiveError(reason instanceof Error ? reason.message : 'Couldn’t load Live settings'); }
            finally { setLiveLoading(false); }
          }}>{liveLoading ? 'Connecting…' : 'Retry'}</Button>
        </div>}
        <label className="block text-zinc-400">Server URL<input disabled={liveLoading || isSaving} value={liveSettings.server_url} onChange={e => { setLiveSettings({ ...liveSettings, server_url: e.target.value }); setLiveDirty(true); }} className={input} /></label>
        <label className="block text-zinc-400">Stream key<input disabled={liveLoading || isSaving} type="password" autoComplete="new-password" value={liveKey} placeholder={liveSettings.key_saved ? 'Saved · leave blank to keep' : 'Paste the Facebook stream key'} onChange={e => { setLiveKey(e.target.value); setLiveDirty(true); }} className={input} /></label>
        <label className="block text-zinc-400">Live Producer URL<input disabled={liveLoading || isSaving} value={liveSettings.producer_url} onChange={e => { setLiveSettings({ ...liveSettings, producer_url: e.target.value }); setLiveDirty(true); }} placeholder="https://www.facebook.com/live/producer/" className={input} /></label>
        {liveLoading && <p className="text-zinc-500">Loading streaming settings…</p>}
      </Section>

      <Section id="settings-automation" title="Automation" description="Changes apply to the next job.">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-zinc-400">Behavior speed
            <Select value={behaviorMode} onValueChange={(value) => { setBehaviorMode(value as BehaviorMode); setSaved(false); }} ariaLabel="Behavior speed" className="mt-1.5"
              options={behaviorOptions.map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }))} />
          </label>
          <label className="text-zinc-400">Reel upload screen
            <Select value={reelTemplate} onValueChange={(value) => { setReelTemplate(value as ReelTemplateSelection); setSaved(false); }} ariaLabel="Reel upload screen" className="mt-1.5"
              options={reelTemplateOptions.map((value) => ({ value, label: reelTemplateLabel(value) }))} />
          </label>
        </div>
        <p className="text-[11px] text-zinc-500">Auto detects which Reel upload screen Facebook shows. Pick one only if detection keeps choosing wrong.</p>
      </Section>

      <section className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/40">
        <button type="button" onClick={() => setAdvancedOpen((open) => !open)} aria-expanded={advancedOpen} aria-controls="settings-advanced"
          className="flex w-full items-center justify-between gap-3 p-4 text-left hover:bg-zinc-900/60">
          <span><span className="block text-sm font-semibold text-zinc-100">Advanced</span>
            <span className="mt-0.5 block text-[11px] text-zinc-500">Screen size, language, rendering, and CPU/memory limits</span></span>
          <ChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 text-zinc-500 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
        </button>
        <div id="settings-advanced" hidden={!advancedOpen} className="space-y-4 border-t border-zinc-800 p-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-zinc-400">Screen size
              <Select value={environment.screen_resolution} onValueChange={(value) => { setEnvironment({ ...environment, screen_resolution: value }); setSaved(false); }}
                ariaLabel="Screen size" className="mt-1.5" options={SCREEN_RESOLUTIONS.map((resolution) => ({ value: resolution, label: resolution }))} />
            </label>
            <label className="text-zinc-400">Language<input value={environment.language} onChange={(e) => setEnvironment({ ...environment, language: e.target.value })} className={input} /></label>
            <label className="text-zinc-400">Rendering
              <Select value={environment.rendering_mode} onValueChange={(value) => { setEnvironment({ ...environment, rendering_mode: value as RequestedEnvironment['rendering_mode'] }); setSaved(false); }}
                ariaLabel="Rendering" className="mt-1.5" options={[{ value: 'host_gpu', label: 'Use this computer’s GPU' }, { value: 'software', label: 'Software' }]} />
            </label>
            <div className="text-zinc-400">User agent<div className={`${input} text-zinc-400`}>Managed by the browser</div></div>
            <label className="text-zinc-400">CPU limit
              <Select value={String(resources.cpu_limit)} onValueChange={(value) => { setResources((current) => ({ ...current, cpu_limit: Number(value) })); setSaved(false); }}
                ariaLabel="CPU limit" className="mt-1.5" options={cpuOptions.map((value) => ({ value: String(value), label: `${value} vCPU` }))} />
            </label>
            <label className="text-zinc-400">Memory limit
              <Select value={String(resources.memory_mb)} onValueChange={(value) => { setResources((current) => ({ ...current, memory_mb: Number(value) })); setSaved(false); }}
                ariaLabel="Memory limit" className="mt-1.5" options={memoryOptions.map((value) => ({ value: String(value), label: `${value / 1024} GiB` }))} />
            </label>
          </div>
          <p className="text-[11px] text-zinc-500">Limits are ceilings, not reserved resources. They apply after a restart.</p>
        </div>
      </section>

      <div className="sticky bottom-0 flex items-center justify-end gap-3 border-t border-zinc-800 bg-background/95 py-3 backdrop-blur">
        {error && <p role="alert" className="mr-auto text-red-300">{error}</p>}
        {saved && <span role="status" className="flex items-center gap-1.5 text-emerald-400"><Check className="h-3.5 w-3.5" />Saved</span>}
        <Button type="button" variant="ghost" disabled={isSaving} onClick={() => setResetKey(key => key + 1)}>Discard changes</Button>
        <Button type="submit" loading={isSaving}>Save changes</Button>
      </div>
    </form>
  </>;
}
