import { useCallback, useEffect, useState } from 'react';
import { Loader2, ShieldCheck } from 'lucide-react';
import { Profile } from '../../types/profile';
import { ProxyItem } from '../../types/proxy';
import { AndroidDevice } from '../../types/device';
import { AccountNetworkStatus, fetchAccountNetwork, verifyAccountNetwork } from '../../services/devices';
import { cn } from '../ui/cn';
import { formatUtcOffset, phoneZone, sameTime } from './phoneTime';

const card = 'rounded-xl border border-zinc-800 bg-zinc-950/40 p-4';

/** Network of a phone account, measured inside its Android user through the companion app. */
export function PhoneNetworkCard({ profile, proxies, devices, onChanged }: {
  profile: Profile; proxies: ProxyItem[]; devices: AndroidDevice[]; onChanged: () => Promise<void> | void;
}) {
  const proxy = profile.network.mode === 'pool' ? proxies.find(p => p.id === profile.network.proxy_id) : undefined;
  const [live, setLive] = useState<AccountNetworkStatus | null>(null);
  const [liveError, setLiveError] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState('');
  const check = profile.android_network;
  const zone = phoneZone(devices.find(d => d.serial === profile.runtime?.device_serial));

  const readLive = useCallback(() => {
    setLiveError('');
    fetchAccountNetwork(profile.id).then(setLive).catch(error => setLiveError(error instanceof Error ? error.message : 'Could not read the phone'));
  }, [profile.id]);
  useEffect(() => { if (proxy) readLive(); }, [proxy, readLive]);

  const verify = async () => {
    setVerifying(true);
    setVerifyError('');
    try {
      await verifyAccountNetwork(profile.id);
      await onChanged();
      readLive();
    } catch (error) {
      setVerifyError(error instanceof Error ? error.message : 'Verify failed');
    } finally {
      setVerifying(false);
    }
  };

  const agent = live?.agent;
  const companion = !live ? (liveError ? liveError : 'Reading…')
    : live.state === 'tunnel_on' ? `Tunnel on · kill switch on · ${agent?.version}`
      : live.reason || 'Not set up';
  const result = check?.status === 'verified' ? { text: 'Verified', tone: 'text-emerald-300' }
    : check?.status === 'blocked' ? { text: 'Blocked', tone: 'text-red-300' }
      : check?.checked_at ? { text: 'Not set up', tone: 'text-zinc-400' } : { text: 'Never checked', tone: 'text-zinc-500' };

  return <section className={card} aria-labelledby="overview-phone-network">
    <h3 id="overview-phone-network" className="mb-3 text-sm font-semibold text-zinc-100">Network on the phone</h3>
    {!proxy ? <p className="text-zinc-400">This account has no proxy, so it uses the phone’s own internet.</p> : <>
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-y-2 text-zinc-400">
        <dt>Proxy</dt><dd className="break-all text-zinc-200">{proxy.host}:{proxy.port}{proxy.exit_ip && proxy.exit_ip !== proxy.host ? ` · exit ${proxy.exit_ip}` : ''}</dd>
        <dt>Companion app</dt><dd className={cn('break-words', live?.state === 'tunnel_on' ? 'text-emerald-300' : live ? 'text-amber-300' : 'text-zinc-500')}>{companion}</dd>
        <dt>Last check</dt>
        <dd><span className={result.tone}>{result.text}</span>
          {check?.checked_at && <span className="ml-2 text-zinc-500">{new Date(check.checked_at).toLocaleString()}</span>}
          {check?.status !== 'verified' && check?.reason && <p className="mt-0.5 break-words text-[11px] text-zinc-400">{check.reason}</p>}</dd>
        {check?.exit_ip && <><dt>Measured exit</dt><dd className="text-zinc-200">{check.exit_ip}{check.exit_timezone ? ` · ${check.exit_timezone}` : ''}</dd></>}
        {zone && proxy.timezone && <><dt>Phone time</dt>
          <dd className={sameTime(zone, proxy.timezone) ? 'text-emerald-300' : 'text-amber-300'}>
            {zone} ({formatUtcOffset(zone)}){sameTime(zone, proxy.timezone) ? ' · matches the proxy' : ` · the proxy is on ${proxy.timezone} (${formatUtcOffset(proxy.timezone)})`}</dd></>}
      </dl>
      <div className="mt-3 flex items-center gap-2">
        <button type="button" onClick={() => void verify()} disabled={verifying}
          className="flex items-center gap-1.5 rounded border border-zinc-700 px-2.5 py-1 text-[11px] text-zinc-200 hover:bg-zinc-800 disabled:opacity-50">
          {verifying ? <Loader2 className="h-3 w-3 animate-spin" /> : <ShieldCheck className="h-3 w-3" />}{verifying ? 'Checking inside the phone…' : 'Verify now'}</button>
        <span className="text-[11px] text-zinc-500">{verifying ? 'About 15 seconds.' : 'Measures the exit IP from inside this Android user.'}</span>
      </div>
      {verifyError && <p role="alert" className="mt-2 break-words text-[11px] text-red-300">{verifyError}</p>}
    </>}
  </section>;
}
