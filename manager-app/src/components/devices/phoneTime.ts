import { AndroidDevice } from '../../types/device';
import { Profile } from '../../types/profile';
import { ProxyItem } from '../../types/proxy';

/** Minutes east of UTC for `zone` right now, or null for an unknown zone. */
export function utcOffsetMinutes(zone: string, at = new Date()): number | null {
  try {
    const name = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'longOffset' })
      .formatToParts(at).find(part => part.type === 'timeZoneName')?.value ?? '';
    const match = /GMT([+-])(\d{2}):(\d{2})/.exec(name);
    return match ? (match[1] === '-' ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3])) : 0;
  } catch {
    return null;
  }
}

export function formatUtcOffset(zone: string) {
  const minutes = utcOffsetMinutes(zone);
  if (minutes === null) return '';
  const sign = minutes < 0 ? '−' : '+';
  const hours = Math.floor(Math.abs(minutes) / 60), rest = Math.abs(minutes) % 60;
  return `UTC${sign}${hours}${rest ? `:${String(rest).padStart(2, '0')}` : ''}`;
}

/** Same clock time now. Names can differ: Phnom Penh and Ho Chi Minh City are both UTC+7. */
export function sameTime(a: string, b: string) {
  const left = utcOffsetMinutes(a), right = utcOffsetMinutes(b);
  return left !== null && left === right;
}

export const phoneZone = (device?: AndroidDevice) => device?.health?.timezone?.zone ?? null;

export interface PhoneTimeReport {
  zone: string | null;
  auto: boolean | null;
  /** Accounts on this phone whose proxy has a known timezone. */
  accounts: { profile: Profile; zone: string }[];
  mismatched: { profile: Profile; zone: string }[];
  /** Accounts on this phone need different clock times; no one phone time fits them all. */
  mixed: boolean;
  /** The time zone every account agrees on, when the phone should be set to it. */
  suggested: string | null;
  /** One account at a time: the phone takes each account's time when it opens, so only the account on screen counts. */
  isolated: boolean;
}

/** Android has one time zone per phone, shared by every Android user on it. */
export function phoneTimeReport(device: AndroidDevice, profiles: Profile[], proxies: ProxyItem[]): PhoneTimeReport {
  const zone = phoneZone(device);
  const auto = device.health?.timezone?.auto ?? null;
  const accounts = profiles
    .filter(profile => profile.runtime?.device_serial === device.serial)
    .map(profile => ({ profile, zone: proxies.find(proxy => proxy.id === profile.network.proxy_id)?.timezone || '' }))
    .filter(entry => entry.zone);
  const isolated = device.one_account_at_a_time !== false;
  const relevant = isolated ? accounts.filter(entry => entry.profile.runtime?.android_user_id === device.health?.current_user) : accounts;
  const mixed = !isolated && accounts.some(entry => !sameTime(entry.zone, accounts[0].zone));
  const mismatched = zone ? relevant.filter(entry => !sameTime(entry.zone, zone)) : [];
  const suggested = !mixed && relevant.length > 0 && (mismatched.length > 0 || auto === true) ? relevant[0].zone : null;
  return { zone, auto, accounts, mismatched, mixed, suggested, isolated };
}
