import { useEffect, useMemo, useState } from 'react';
import { Globe, Loader2, ScanLine } from 'lucide-react';
import { AndroidDevice } from '../../types/device';
import { freeProxies, isPoorProxy, proxyOptionLabel, ProxyItem } from '../../types/proxy';
import { SavedProxyCheck } from '../SavedProxyCheck';
import { formatUtcOffset, phoneZone, sameTime } from './phoneTime';
import { scanDevice } from '../../services/devices';
import { ProfileGroupField } from '../ProfileGroupField';
import { Select } from '../ui/Select';

export interface PhoneAccountRequest { name: string; group: string; proxyId: string | null }
const OWN_NETWORK = 'phone-network';

/** New account on an Android user of a phone. Users that already hold an account are not offered. */
export function PhoneAccountForm({ devices, preset, proxies, groups, onCreate, onCancel, onProxiesChanged }: {
  devices: AndroidDevice[]; preset?: { serial: string; userId: number } | null;
  proxies: ProxyItem[]; groups: string[];
  onCreate: (serial: string, userId: number, request: PhoneAccountRequest) => Promise<void>;
  onCancel: () => void;
  onProxiesChanged: () => Promise<void> | void;
}) {
  const [phones, setPhones] = useState(devices);
  useEffect(() => setPhones(devices), [devices]);
  const [serial, setSerial] = useState(preset?.serial ?? devices.find(d => d.state === 'device')?.serial ?? devices[0]?.serial ?? '');
  const phone = phones.find(d => d.serial === serial);
  const freeUsers = useMemo(() => (phone?.last_scan?.users ?? []).filter(user => !user.control_user && !user.profile_id), [phone]);
  const [userId, setUserId] = useState<number | null>(preset?.userId ?? null);
  const user = freeUsers.find(row => row.user_id === userId);
  const [name, setName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  const [group, setGroup] = useState('');
  // The phone has one time zone for all its accounts, so proxies on the phone's time come first.
  const zone = phoneZone(phone);
  const matchesPhone = (proxy: ProxyItem) => Boolean(zone && proxy.timezone && sameTime(proxy.timezone, zone));
  const available = freeProxies(proxies).sort((a, b) => Number(matchesPhone(b)) - Number(matchesPhone(a)));
  const [proxyChoice, setProxyChoice] = useState(available.find(proxy => !isPoorProxy(proxy))?.id ?? OWN_NETWORK);
  const [scanning, setScanning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the chosen user valid for the chosen phone, and name the account after it until the user types.
  useEffect(() => {
    if (!freeUsers.some(row => row.user_id === userId)) setUserId(freeUsers[0]?.user_id ?? null);
  }, [freeUsers, userId]);
  useEffect(() => { if (!nameTouched) setName(user?.name ?? ''); }, [user, nameTouched]);

  const scan = async () => {
    if (!phone) return;
    setScanning(true);
    setError(null);
    try {
      const scanned = await scanDevice(phone.serial);
      setPhones(current => current.map(d => d.serial === phone.serial ? { ...d, ...scanned } : d));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving) return;
    if (!phone || userId === null) { setError('Choose a phone and a free Android user'); return; }
    if (!name.trim()) { setError('Enter an account name'); return; }
    setSaving(true);
    setError(null);
    try {
      await onCreate(phone.serial, userId, { name: name.trim(), group: group.trim(), proxyId: proxyChoice === OWN_NETWORK ? null : proxyChoice });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the account');
      setSaving(false);
    }
  };

  const selectedProxy = available.find(proxy => proxy.id === proxyChoice);
  return <form onSubmit={submit} aria-busy={saving} className="flex min-h-0 flex-1 flex-col">
    <div className="flex-1 space-y-4 overflow-y-auto p-5 text-xs">
      <fieldset disabled={saving} className="contents space-y-4">
        {phones.length === 0
          ? <p className="rounded border border-zinc-800 bg-zinc-950 p-3 text-zinc-400">No phones are connected. Connect one on the Devices page first.</p>
          : <div className="grid grid-cols-2 gap-2">
            <label className="block text-zinc-400"><span className="mb-1.5 block font-medium">Phone</span>
              <Select value={serial} onValueChange={(value) => setSerial(value)} ariaLabel="Phone"
                options={phones.map(d => ({ value: d.serial, label: d.state === 'device' ? d.label : `${d.label} (offline)` }))} />
            </label>
            <label className="block text-zinc-400"><span className="mb-1.5 block font-medium">Android user</span>
              {freeUsers.length > 0
                ? <Select value={userId === null ? '' : String(userId)} onValueChange={(value) => setUserId(Number(value))} ariaLabel="Android user"
                  options={freeUsers.map(row => ({ value: String(row.user_id), label: `${row.name} · User ${row.user_id}` }))} />
                : <button type="button" onClick={() => void scan()} disabled={!phone || phone.state !== 'device' || scanning}
                  className="flex w-full items-center justify-center gap-1.5 rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-300 disabled:opacity-50">
                  {scanning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ScanLine className="h-3.5 w-3.5" />}
                  {phone?.last_scan ? 'No free users · scan again' : 'Scan phone'}</button>}
            </label>
          </div>}
        {user?.facebook_installed === false && <p className="rounded border border-amber-900/50 bg-amber-950/20 p-2 text-amber-300">Facebook is not installed for this Android user yet.</p>}

        <label className="block text-zinc-400">
          <span className="mb-1.5 block font-medium">Account name</span>
          <input required value={name} onChange={(e) => { setName(e.target.value); setNameTouched(true); }} maxLength={120}
            className="w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100 focus:border-zinc-600 focus:outline-none" />
        </label>
        <ProfileGroupField value={group} onChange={setGroup} groups={groups} />

        <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
          <div className="flex items-center gap-1.5 font-medium text-zinc-300"><Globe className="h-3.5 w-3.5" />Proxy</div>
          <Select value={proxyChoice} onValueChange={setProxyChoice} ariaLabel="Proxy"
            options={[
              ...available.map(proxy => ({ value: proxy.id,
                label: zone && proxy.timezone ? `${proxyOptionLabel(proxy)} · ${matchesPhone(proxy) ? 'phone time ✓' : 'other time'}` : proxyOptionLabel(proxy) })),
              { value: OWN_NETWORK, label: 'No proxy for now' },
            ]} />
          {selectedProxy && <SavedProxyCheck proxy={selectedProxy} disabled={saving} onRetested={onProxiesChanged} />}
          {selectedProxy && zone && selectedProxy.timezone && !matchesPhone(selectedProxy) && (phone?.one_account_at_a_time !== false
            ? <p className="text-[11px] text-zinc-400">
              This proxy is in {selectedProxy.timezone} ({formatUtcOffset(selectedProxy.timezone)}). The phone runs one account at a time,
              so it switches to this time whenever this account opens.</p>
            : <p className="text-[11px] text-amber-300">
              This proxy is in {selectedProxy.timezone} ({formatUtcOffset(selectedProxy.timezone)}), but the phone is on {zone} ({formatUtcOffset(zone)}).
              Every account on a phone shares its time, so pick a proxy marked “phone time ✓”, or turn on One account at a time for this phone.</p>)}
          {selectedProxy && isPoorProxy(selectedProxy) && <p className="text-[11px] text-amber-300">This proxy failed its last speed check (below 1 Mbps or the test failed). Pick a Good one if you can.</p>}
          <p className="text-[11px] text-zinc-500">{selectedProxy
            ? 'Only free proxies are listed, best first. This one is reserved for this account and used once the companion app is set up on the phone.'
            : 'The phone uses its own network. You can’t add a proxy to a phone account later yet.'}</p>
        </div>
        {error && <p className="rounded border border-red-900/50 bg-red-950/30 p-2 text-red-300">{error}</p>}
        <p className="text-[11px] text-zinc-500">Log in to Facebook on the phone yourself after the account is created.</p>
      </fieldset>
    </div>
    <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
      <button type="button" disabled={saving} onClick={onCancel} className="rounded border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300 disabled:opacity-40">Cancel</button>
      <button disabled={saving || !phone || userId === null} type="submit" className="flex items-center gap-2 rounded bg-white px-4 py-1.5 font-semibold text-zinc-950 disabled:opacity-50">
        {saving && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}{saving ? 'Creating…' : 'Create account'}
      </button>
    </div>
  </form>;
}
