import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';
import { Profile } from '../../types/profile';
import { ProxyItem } from '../../types/proxy';
import { QueueExecutionItem } from '../../types/automation';
import { AndroidDevice } from '../../types/device';
import { checkProfile, checkProfileFacebookResponse, checkProfileProxySpeed, FacebookResponseResult, ProxySpeedResult } from '../../services/api';
import { Button } from '../ui/Button';
import { cn } from '../ui/cn';
import { FAILED_STATUSES, describe, formatTime, kindLabel } from '../queue/queueModel';
import { AccountActivity, AccountTab, STATUS_LABEL, STATUS_TONE, TONE_CLASS, browserNotes, isAndroid, liveSignal, networkSignal, openItemSignals, phoneStatus } from './accountModel';
import { STAGE_META, StageBadge, StageView, refreshAccountHealth, restartEnds, stageView, todayCount, useAccountHealth } from './accountHealth';
import { Alert } from '../ui/Alert';
import { DeviceChip } from './DeviceChip';
import { PhoneNetworkCard } from '../devices/PhoneNetworkCard';

const card = 'rounded-xl border border-zinc-800 bg-zinc-950/40 p-4';

export function AccountOverview({ profile, proxies, devices, activity, onOpenQueue, onOpenTab, onToggleHold, onOpenDevice, onChanged }: {
  profile: Profile; proxies: ProxyItem[]; devices: AndroidDevice[]; activity: AccountActivity;
  /** Opens the queue for this account, with one item's details open when given. */
  onOpenQueue: (executionId?: string) => void;
  onOpenTab: (tab: AccountTab) => void; onToggleHold: () => void;
  onOpenDevice?: (serial: string | null) => void;
  /** Reloads accounts after a check saved new results. */
  onChanged: () => Promise<void> | void;
}) {
  const network = networkSignal(profile, proxies);
  const android = isAndroid(profile);
  const { health } = useAccountHealth();
  const stage = stageView(profile, health[profile.id], activity);
  const upcoming = activity.recent.filter(item => ['pending', 'ready'].includes(item.status))
    .sort((a, b) => (new Date(a.scheduled_at).getTime() || 0) - (new Date(b.scheduled_at).getTime() || 0)).slice(0, 5);
  return <div className="mx-auto grid max-w-5xl gap-4 text-xs lg:grid-cols-2">
    {android && <Alert className="lg:col-span-2">This account is on a phone. Use the Screen tab to see and control it. {profile.network.mode !== 'pool'
      ? 'It has no proxy, so it uses the phone’s own internet.'
      : profile.android_network?.status === 'verified' ? 'Its proxy was verified inside the phone.'
        : 'Don’t use Facebook on this account until Verify now shows its proxy working on the phone.'} Automation for phone accounts isn’t available yet, so the queue skips it.</Alert>}
    <section className={card} aria-labelledby="overview-status">
      <h3 id="overview-status" className="mb-3 text-sm font-semibold text-zinc-100">Status</h3>
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-y-2 text-zinc-400">
        {android && profile.runtime
          ? <><dt>Phone</dt><dd><DeviceChip profile={profile} devices={devices} onClick={onOpenDevice && (() => onOpenDevice(profile.runtime!.device_serial))} />
              <span className={cn('ml-2', phoneStatus(profile, devices).tone)}>{phoneStatus(profile, devices).label}</span></dd>
            <dt>Android user</dt><dd className="text-zinc-200">{profile.runtime.android_user_name || 'User'} · User {profile.runtime.android_user_id}</dd></>
          : <><dt>Browser</dt><dd><span className={STATUS_TONE[profile.status]}>{STATUS_LABEL[profile.status]}</span>
            {browserNotes(profile).filter(n => n.label !== 'Browser error').map(n => <span key={n.label} title={n.hint} className={cn('ml-2', TONE_CLASS[n.tone])}>{n.label}</span>)}</dd></>}
        {profile.status === 'running' && <><dt>CPU</dt><dd className="text-zinc-200">{profile.cpu_usage || '0%'}</dd>
          <dt>Memory</dt><dd className="text-zinc-200">{profile.ram_usage || 'Not measured'}</dd></>}
        <dt>Network</dt><dd title={network.hint} className={TONE_CLASS[network.tone]}>{network.label}{network.detail && <span className="ml-2 text-zinc-400">{network.detail}</span>}</dd>
        <dt>Facebook Live</dt><dd title={liveSignal(profile).hint} className={TONE_CLASS[liveSignal(profile).tone]}>{liveSignal(profile).label}</dd>
        <dt>Stage</dt><dd className="space-y-0.5">
          <span className="flex items-center gap-2">
            <StageBadge view={stage} />
            {!android && <button type="button" onClick={onToggleHold} className="text-[11px] text-zinc-500 underline decoration-dotted underline-offset-2 hover:text-zinc-300 hover:decoration-solid">
              {profile.account?.on_hold ? 'Release hold' : 'Put on hold…'}</button>}
          </span>
          <p className="text-zinc-400">{stage.reason}{todayCount(stage) && ` ${todayCount(stage)}`}</p>
          {stage.evidence && stage.evidence.length > 0 && <div className="py-0.5">
            <p className="text-[11px] font-medium text-zinc-500">Evidence</p>
            <ul className="space-y-1">{stage.evidence.map((item, index) => <li key={item.executionId || `${item.label}-${index}`} className="min-w-0">
              <button type="button" onClick={() => item.executionId ? onOpenQueue(item.executionId) : item.tab && onOpenTab(item.tab)}
                className="text-left text-zinc-200 underline decoration-dotted underline-offset-2 hover:decoration-solid">{item.label}</button>
              {item.at && <span className="text-zinc-500"> · {formatTime(item.at)}</span>}
              {item.detail && <span title={item.detail} className="block truncate text-zinc-500">{item.detail}</span>}
            </li>)}</ul>
          </div>}
          {stage.stage !== 'on_hold' && stage.health?.proxy_restart && <p className="text-amber-300/90">
            Back to normal {restartEnds(stage.health)}.{stage.health.proxy_restart.visited ? '' : ' Posts wait for the first warming visit from the new IP.'}</p>}
          <p className="text-zinc-500">{STAGE_META[stage.stage].suggestion}{stage.health?.manual ? ' Set by hand in Settings.' : ''}</p>
          {!android && <AccountCheck profile={profile} stage={stage} recent={activity.recent} onOpenQueue={onOpenQueue} />}
        </dd>
        <dt>Today</dt><dd className="text-zinc-200">{activity.posted} posted{activity.warmed ? ` · ${activity.warmed} warmed` : ''}</dd>
        {openItemSignals(activity).length > 0 && <><dt>Needs you</dt><dd className="flex flex-wrap gap-x-2">{openItemSignals(activity).map(s =>
          <button key={s.label} type="button" title={s.hint} onClick={() => onOpenQueue()} className={cn('underline decoration-dotted underline-offset-2 hover:decoration-solid', TONE_CLASS[s.tone])}>{s.label}</button>)}</dd></>}
        {profile.account.notes && <><dt>Notes</dt><dd className="whitespace-pre-wrap text-zinc-300">{profile.account.notes}</dd></>}
      </dl>
    </section>

    {android ? <PhoneNetworkCard profile={profile} proxies={proxies} devices={devices} onChanged={onChanged} /> : <NetworkChecks profile={profile} />}

    <section className={cn(card, 'lg:col-span-2')} aria-labelledby="overview-work">
      <div className="mb-3 flex items-center justify-between">
        <h3 id="overview-work" className="text-sm font-semibold text-zinc-100">Work</h3>
        <Button size="sm" variant="ghost" onClick={() => onOpenQueue()}>Open queue<ArrowRight className="h-3.5 w-3.5" /></Button>
      </div>
      {!activity.attention.length && !activity.running && !upcoming.length
        ? <p className="text-zinc-500">Nothing queued for this account.</p>
        : <ul className="divide-y divide-zinc-800/70">
          {activity.attention.map(item => <WorkItem key={item.execution_id} item={item}
            tone={FAILED_STATUSES.has(item.status) && item.status !== 'failed_after_publish' ? 'text-red-300' : 'text-amber-300'}
            label={item.status === 'published' ? 'Comment' : FAILED_STATUSES.has(item.status) && item.status !== 'failed_after_publish' ? 'Failed' : 'Unconfirmed'} />)}
          {activity.running && <WorkItem item={activity.running} tone="text-blue-300" label="Now" />}
          {upcoming.map(item => <WorkItem key={item.execution_id} item={item} tone="text-zinc-300" label={formatTime(item.scheduled_at)} />)}
        </ul>}
    </section>

    {!android && <TechnicalDetails profile={profile} />}
  </div>;
}

const CHECK_BATCH = 'account_check_';
const OPEN_CHECK = new Set(['pending', 'ready', 'preparing', 'running']);
const CHECK_RESULT: Record<string, string> = {
  completed: 'passed', skipped_auth_required: 'Facebook still asks to log in or verify',
  skipped_personal_profile: 'still on the personal profile',
};

/** Check now: after fixing an account, one read-only visit confirms it and clears Needs attention. */
function AccountCheck({ profile, stage, recent, onOpenQueue }: {
  profile: Profile; stage: StageView; recent: QueueExecutionItem[]; onOpenQueue: (executionId?: string) => void;
}) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const last = recent.find(item => item.batch_id?.startsWith(CHECK_BATCH));
  const running = Boolean(last && OPEN_CHECK.has(last.status));
  const wasRunning = useRef(running);
  // A check that just finished changes the stage; reload it now instead of on the next poll.
  useEffect(() => {
    if (wasRunning.current && !running) refreshAccountHealth();
    wasRunning.current = running;
  }, [running]);
  // Only history problems (login, checkpoint, personal profile, failures) are cleared by a check;
  // open queue items are resolved in the Queue.
  const checkable = stage.health?.stage === 'attention';
  if (!checkable && !last) return null;
  const start = async () => {
    setStarting(true);
    setError('');
    try { await checkProfile(profile.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Couldn’t start the check.'); }
    finally { setStarting(false); }
  };
  const result = last && !running ? CHECK_RESULT[last.status] || last.error || describe(last) : '';
  return <div className="space-y-1 pt-1">
    {checkable && !profile.account?.on_hold && <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="outline" onClick={start} disabled={starting || running}>
        {starting || running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
        {running ? 'Checking…' : 'Check now'}
      </Button>
      <span className="text-[11px] text-zinc-500">{running
        ? 'Opening Facebook to confirm the account. About a minute.'
        : 'Fixed it? One short read-only visit confirms it and moves the account out of Needs attention.'}</span>
    </div>}
    {last && !running && <p className="text-[11px] text-zinc-500">
      Last check {formatTime(last.stage_updated_at || last.scheduled_at)}:{' '}
      <button type="button" onClick={() => onOpenQueue(last.execution_id)}
        className={cn('underline decoration-dotted underline-offset-2 hover:decoration-solid', last.status === 'completed' ? 'text-emerald-300' : 'text-amber-300')}>{result}</button>
    </p>}
    {error && <p role="alert" className="text-[11px] text-red-400">{error}</p>}
  </div>;
}

function WorkItem({ item, label, tone }: { item: QueueExecutionItem; label: string; tone: string }) {
  return <li className="grid grid-cols-[6rem_minmax(0,1fr)] gap-3 py-2">
    <span className={cn('font-mono', tone)}>{label}</span>
    <span className="min-w-0"><span className="text-zinc-200">{kindLabel(item)}</span>{item.batch_name && <span className="text-zinc-600"> · {item.batch_name}</span>}
      <span className="block truncate text-zinc-500">{describe(item)}</span></span>
  </li>;
}

function NetworkChecks({ profile }: { profile: Profile }) {
  const [facebook, setFacebook] = useState<FacebookResponseResult | null>(null);
  const [facebookBusy, setFacebookBusy] = useState(false);
  const [facebookError, setFacebookError] = useState<string | null>(null);
  const [download, setDownload] = useState<ProxySpeedResult | null>(null);
  const [upload, setUpload] = useState<ProxySpeedResult | null>(null);
  const [speedPhase, setSpeedPhase] = useState<'download' | 'upload' | null>(null);
  const [speedError, setSpeedError] = useState<string | null>(null);
  const request = useRef(0);
  const network = profile.network;

  // A different account or proxy invalidates earlier results and in-flight checks.
  useEffect(() => {
    request.current += 1;
    setFacebook(null); setFacebookError(null); setFacebookBusy(false);
    setDownload(null); setUpload(null); setSpeedPhase(null); setSpeedError(null);
  }, [profile.id, network.proxy_host, network.proxy_port, network.proxy_type, network.proxy_user, network.proxy_pass]);

  const checkFacebook = async () => {
    const id = ++request.current;
    setFacebookBusy(true); setFacebookError(null);
    try { const result = await checkProfileFacebookResponse(profile.id); if (id === request.current) setFacebook(result); }
    catch (error) { if (id === request.current) setFacebookError(error instanceof Error ? error.message : 'Check failed'); }
    finally { if (id === request.current) setFacebookBusy(false); }
  };
  const checkSpeed = async () => {
    const id = ++request.current;
    setSpeedPhase('download'); setSpeedError(null); setDownload(null); setUpload(null);
    try {
      const down = await checkProfileProxySpeed(profile.id, 'download');
      if (id !== request.current) return;
      setDownload(down); setSpeedPhase('upload');
      const up = await checkProfileProxySpeed(profile.id, 'upload');
      if (id === request.current) setUpload(up);
    } catch (error) { if (id === request.current) setSpeedError(error instanceof Error ? error.message : 'Speed test failed'); }
    finally { if (id === request.current) setSpeedPhase(null); }
  };

  const preflight = profile.effective_environment?.network_preflight;
  const mbps = (result: ProxySpeedResult | null, phase: 'download' | 'upload') =>
    speedPhase === phase ? 'Checking…' : result?.mbps != null ? `${result.mbps} Mbps` : result ? 'Failed' : '—';

  return <section className={card} aria-labelledby="overview-network">
    <h3 id="overview-network" className="mb-3 text-sm font-semibold text-zinc-100">Network</h3>
    <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-y-2 text-zinc-400">
      <dt>Connection</dt><dd className="break-all text-zinc-200">{network.proxy_host ? `Proxy · ${network.proxy_host}:${network.proxy_port}` : 'Direct'}</dd>
      <dt>Timezone</dt><dd className="text-zinc-200">{profile.effective_environment?.timezone || profile.requested_environment?.timezone || profile.fingerprint.timezone}</dd>
      {preflight && <><dt>Last start check</dt><dd className={preflight.startsWith('passed:') ? 'text-emerald-300' : 'text-red-300'}>{preflight.startsWith('passed:') ? 'Passed' : preflight}</dd></>}
      {network.proxy_host && <>
        <dt>Facebook response</dt>
        <dd className="flex items-center gap-2">
          <span className={facebook && !facebook.success ? 'text-red-300' : 'text-zinc-200'}>
            {facebookBusy ? 'Checking…' : facebook?.response_ms != null ? `${facebook.response_ms} ms` : facebook ? 'Failed' : '—'}</span>
          <button type="button" onClick={checkFacebook} disabled={facebookBusy || Boolean(speedPhase)} aria-label="Check Facebook response time"
            className="rounded border border-zinc-700 p-1 text-zinc-400 hover:bg-zinc-800 hover:text-white disabled:opacity-50">
            {facebookBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}</button>
        </dd>
        <dt>Speed</dt>
        <dd className="flex flex-wrap items-center gap-2 text-zinc-200">
          <span>↓ {mbps(download, 'download')}</span><span>↑ {mbps(upload, 'upload')}</span>
          <button type="button" onClick={checkSpeed} disabled={Boolean(speedPhase) || facebookBusy} title="Uses up to 7 MB of test data per direction."
            className="rounded border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-400 hover:bg-zinc-800 hover:text-white disabled:opacity-50">{speedPhase ? 'Testing…' : 'Test speed'}</button>
        </dd>
      </>}
    </dl>
    {[facebookError || facebook?.error, download?.error && `Download: ${download.error}`, upload?.error && `Upload: ${upload.error}`, speedError]
      .filter(Boolean).map(message => <p key={message as string} role="alert" className="mt-2 break-words text-[11px] text-red-300">{message}</p>)}
  </section>;
}

function TechnicalDetails({ profile }: { profile: Profile }) {
  const requested = profile.requested_environment;
  const effective = profile.effective_environment;
  const observed = profile.observed_environment;
  const resources = profile.resources || { cpu_limit: 4, memory_mb: 4096 };
  return <details className={cn(card, 'lg:col-span-2 text-zinc-400')}>
    <summary className="cursor-pointer text-sm font-semibold text-zinc-300">Technical details</summary>
    <dl className="mt-3 grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-1.5">
      <dt>Account ID</dt><dd className="break-all font-mono text-zinc-300">{profile.id}</dd>
      <dt>Requested</dt><dd className="text-zinc-300">{requested ? `${requested.screen_resolution} · ${requested.timezone || 'proxy timezone'} · ${requested.language} · ${requested.rendering_mode === 'host_gpu' ? 'GPU' : 'Software'}` : `${profile.fingerprint.screen_resolution} · ${profile.fingerprint.timezone}`}</dd>
      <dt>Applied</dt><dd className="text-zinc-300">{effective ? `${effective.screen_resolution} · ${effective.timezone}` : 'Not applied yet'}</dd>
      <dt>Seen in browser</dt><dd className="text-zinc-300">{observed ? `${observed.screen_resolution || '?'} · ${observed.timezone || '?'} · ${observed.language || '?'}` : 'Not measured'}</dd>
      {observed?.webgl_renderer && <><dt>Renderer</dt><dd className="break-words text-zinc-300">{observed.webgl_renderer}{observed.rendering && !observed.rendering.accelerated ? ' (software fallback)' : ''}</dd></>}
      {observed?.browser_version && <><dt>Browser</dt><dd className="break-words text-zinc-300">{observed.browser_version}</dd></>}
      <dt>Limits</dt><dd className="text-zinc-300">{resources.cpu_limit} vCPU · {resources.memory_mb / 1024} GiB{profile.effective_resources ? ` (applied ${profile.effective_resources.cpu_limit} vCPU · ${profile.effective_resources.memory_mb / 1024} GiB)` : ''}</dd>
      <dt>Network isolation</dt><dd className="text-zinc-300">{profile.network.proxy_host ? 'Blocked if the proxy drops' : 'Not used with a direct connection'}</dd>
      <dt>Display ports</dt><dd className="font-mono text-zinc-300">VNC {profile.container.vnc_port} · WS {profile.container.ws_port || profile.container.vnc_port + 180}</dd>
      <dt>Data folder</dt><dd className="break-all font-mono text-zinc-300">{profile.container.volume_path}</dd>
    </dl>
  </details>;
}
