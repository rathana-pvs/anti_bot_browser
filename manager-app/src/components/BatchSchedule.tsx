import { useEffect, useMemo, useState } from 'react';
import { Calendar, Clock, Moon, Sun, Zap } from 'lucide-react';
import { fetchSchedulePreview, SchedulePreview, SchedulePreviewAccount } from '../services/api';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';
import { clockIn, zonePlace } from './queue/queueModel';

/** Every account gets the same start; they begin as job slots allow. */
export const ALL_AT_ONCE = -1;

export const SPREAD_OPTIONS = [
  { value: String(ALL_AT_ONCE), label: 'All at once · as slots free up' },
  { value: '0', label: 'Back to back · 45–120 s apart' },
  { value: '15', label: 'Spread over 15 min' },
  { value: '30', label: 'Spread over 30 min' },
  { value: '60', label: 'Spread over 1 hour' },
  { value: '120', label: 'Spread over 2 hours' },
  { value: '240', label: 'Spread over 4 hours' },
];

export function spreadHint(spread: number): string {
  if (spread === ALL_AT_ONCE) return 'Every account starts right away, as many together as your job slots allow, at least 10 s apart.';
  if (spread > 0) return `Each account starts at its own random moment within ${spread >= 60 ? `${spread / 60} h` : `${spread} min`}.`;
  return 'One after another, with a random gap each time.';
}

const pad = (n: number) => String(n).padStart(2, '0');

/** "2026-11-07" in this PC's time. */
export function localDate(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The start moment from the picked day and clock, in this PC's time. */
export function startFrom(date: string, time: string): Date | null {
  const [y, m, d] = date.split('-').map(Number);
  const [hh, mm] = time.split(':').map(Number);
  if (![y, m, d, hh, mm].every(Number.isFinite)) return null;
  const at = new Date(y, m - 1, d, hh, mm, 0, 0);
  return Number.isNaN(at.getTime()) ? null : at;
}

/** The saved clock time, on the next day it is still ahead. */
export function defaultStart(clock: string): { date: string; time: string } {
  const time = /^\d{2}:\d{2}$/.test(clock) ? clock : '09:00';
  const today = localDate(new Date());
  const at = startFrom(today, time);
  if (at && at.getTime() > Date.now() + 5 * 60_000) return { date: today, time };
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  return { date: localDate(tomorrow), time };
}

/** A past start is refused by the queue; a minute of slack covers a form left open. */
export function startProblem(date: string, time: string): string | null {
  const at = startFrom(date, time);
  if (!at) return 'Pick the date and time to start.';
  if (at.getTime() < Date.now() - 60_000) return 'That start time has already passed. Pick a later time or use Start now.';
  return null;
}

function hourIn(zone: string, at: Date) {
  try {
    return Number(new Intl.DateTimeFormat('en-GB', { timeZone: zone, hour: '2-digit', hourCycle: 'h23' }).format(at));
  } catch { return NaN; }
}

function awakeAt(account: SchedulePreviewAccount, at: Date) {
  if (!account.awake_hours || !account.timezone) return true;
  const [start, end] = account.awake_hours;
  const length = (end - start + 24) % 24 || 24;
  return (hourIn(account.timezone, at) - start + 24) % 24 < length;
}

/** The next full hour, within two days, when every selected account is awake. */
function nextAllAwake(accounts: SchedulePreviewAccount[], from = new Date()): Date | null {
  const at = new Date(from);
  at.setMinutes(0, 0, 0);
  for (let i = 1; i <= 48; i++) {
    at.setHours(at.getHours() + 1);
    if (accounts.every(a => awakeAt(a, at))) return new Date(at);
  }
  return null;
}

/** "17:05" as 5:05 PM. */
function from24(time: string) {
  const [h, m] = time.split(':').map(Number);
  const hour = Number.isFinite(h) ? h : 9;
  return { hour12: hour % 12 || 12, minute: Number.isFinite(m) ? m : 0, pm: hour >= 12 };
}

function to24(hour12: number, minute: number, pm: boolean) {
  return `${pad((hour12 % 12) + (pm ? 12 : 0))}:${pad(minute)}`;
}

const HOUR_OPTIONS = Array.from({ length: 12 }, (_, i) => ({ value: String(i + 1), label: String(i + 1) }));

/** Every 5 minutes, plus an odd minute already chosen so it still shows. */
function minuteOptions(current: number) {
  const minutes = Array.from({ length: 12 }, (_, i) => i * 5);
  if (!minutes.includes(current)) minutes.push(current);
  return minutes.sort((a, b) => a - b).map(m => ({ value: pad(m), label: pad(m) }));
}

/** Today and the next 13 days, named in words. */
function dayOptions(current: string) {
  const days = Array.from({ length: 14 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() + i);
    const label = i === 0 ? `Today · ${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}`
      : i === 1 ? `Tomorrow · ${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}`
        : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    return { value: localDate(d), label };
  });
  if (!days.some(day => day.value === current)) {
    const at = startFrom(current, '12:00');
    days.unshift({ value: current, label: at ? at.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) : current });
  }
  return days;
}

/** Up to the next 5 minutes, so quick picks land on the minute list. */
function roundUp(at: Date) {
  const d = new Date(at);
  d.setSeconds(0, 0);
  d.setMinutes(Math.ceil(d.getMinutes() / 5) * 5);
  return d;
}

/** Day, hour, minute and AM/PM dropdowns for a start time on this PC's clock. */
export function StartPicker({ date, time, onStart }: { date: string; time: string; onStart: (date: string, time: string) => void }) {
  const hm = from24(time);
  return <div className="grid grid-cols-[minmax(0,1fr)_3.75rem_3.75rem_4rem] gap-1.5">
    <Select value={date} onValueChange={value => onStart(value, time)} ariaLabel="Start day" options={dayOptions(date)} />
    <Select value={String(hm.hour12)} onValueChange={value => onStart(date, to24(Number(value), hm.minute, hm.pm))} ariaLabel="Start hour"
      options={HOUR_OPTIONS} className="px-2" />
    <Select value={pad(hm.minute)} onValueChange={value => onStart(date, to24(hm.hour12, Number(value), hm.pm))} ariaLabel="Start minute"
      options={minuteOptions(hm.minute)} className="px-2" />
    <Select value={hm.pm ? 'PM' : 'AM'} onValueChange={value => onStart(date, to24(hm.hour12, hm.minute, value === 'PM'))} ariaLabel="AM or PM"
      options={[{ value: 'AM', label: 'AM' }, { value: 'PM', label: 'PM' }]} className="px-2" />
  </div>;
}

const yourTime = (at: Date) => at.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });

interface Props {
  profileIds: string[];
  postsPerAccount: number;
  mode: 'now' | 'scheduled';
  onMode: (mode: 'now' | 'scheduled') => void;
  date: string;
  time: string;
  onStart: (date: string, time: string) => void;
  spread: number;
  onSpread: (minutes: number) => void;
  follow: boolean;
  onFollow: (on: boolean) => void;
  /** The plan the form shows (Follow active hours), so the batch gets the same times. */
  onPlan: (seed: number | null, problem: string | null) => void;
  /** Editing a batch: its own waiting jobs are planned again, so they don't block the preview. */
  excludeBatchId?: string;
}

const dayWord = (at: Date) => {
  const days = Math.round((new Date(at).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86_400_000);
  return days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : at.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
};
const clock = (at: Date) => at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

/** When a batch starts. Start now: right away, whatever the hour. Schedule with Follow active
 * hours: no time to enter, each account's posts are planned in its own day. Schedule without
 * it: the date and time picked here, for every account. */
export function BatchSchedule({ profileIds, postsPerAccount, mode, onMode, date, time, onStart, spread, onSpread, follow, onFollow, onPlan, excludeBatchId }: Props) {
  const scheduled = mode === 'scheduled';
  const auto = scheduled && follow;
  const exact = scheduled && !follow;
  const start = exact ? startFrom(date, time) : new Date();
  const problem = exact ? startProblem(date, time) : null;
  const [preview, setPreview] = useState<SchedulePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const ids = profileIds.join(',');

  useEffect(() => {
    setPreview(null);
    if (!profileIds.length || problem || mode === 'now') { onPlan(null, null); return; }
    let live = true;
    const timer = window.setTimeout(() => {
      fetchSchedulePreview(profileIds, auto
        ? { timing: 'auto' }
        : { timing: 'exact', start_at: startFrom(date, time)!.toISOString(), spread_minutes: spread }, postsPerAccount, excludeBatchId)
        .then(next => {
          if (!live) return;
          setPreview(next);
          setPreviewError(null);
          const stuck = next.accounts.find(a => a.error);
          onPlan(auto ? next.plan_seed : null, stuck ? `${stuck.name}: ${stuck.error}` : null);
        })
        .catch(err => { if (live) { setPreviewError(err.message || String(err)); onPlan(null, null); } });
    }, 350);
    return () => { live = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, mode, auto, date, time, spread, problem, postsPerAccount]);

  const accounts = preview?.accounts || [];
  const zones = useMemo(() => [...new Set(accounts.map(a => a.timezone).filter((z): z is string => !!z))], [accounts]);
  const atNight = exact ? accounts.filter(a => !a.awake_at_start || !a.awake_at_end) : [];
  const morning = useMemo(() => accounts.length ? nextAllAwake(accounts) : null, [accounts]);
  const later = auto ? accounts.filter(a => (a.planned_at || []).some(t => dayWord(new Date(t)) !== 'Today' && dayWord(new Date(t)) !== 'Tomorrow')) : [];
  const plan = preview?.plan;

  const pick = (at: Date) => onStart(localDate(at), `${pad(at.getHours())}:${pad(at.getMinutes())}`);
  const chip = 'rounded-md border border-zinc-800 px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:border-zinc-700 hover:text-zinc-200';

  return <div className="space-y-3">
    <div className="grid grid-cols-2 gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
      {(['now', 'scheduled'] as const).map(option => <button key={option} type="button" onClick={() => onMode(option)}
        className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-all ${mode === option ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}>
        {option === 'now' ? <Zap className="h-3.5 w-3.5" /> : <Calendar className="h-3.5 w-3.5" />}
        <span>{option === 'now' ? 'Start now' : 'Schedule'}</span>
      </button>)}
    </div>

    {mode === 'now' && <p className="text-[11px] text-zinc-500">Every account starts right away, whatever the time is where it is.</p>}

    {scheduled && <label htmlFor="follow-active-hours" className="flex cursor-pointer items-start justify-between gap-3 text-xs">
      <span>
        <span className="block text-zinc-200">Follow active hours</span>
        <span className="block text-[11px] text-zinc-500">{follow
          ? `The app picks each account’s time in its own day${plan ? `: ${plan.min_gap_hours}–${plan.max_gap_hours} h between its posts, up to ${plan.max_posts_per_day} a day` : ''}. Change these in Settings.`
          : 'Every account starts at the time you pick below, even at night where its proxy is.'}</span>
      </span>
      <Checkbox id="follow-active-hours" checked={follow} onCheckedChange={onFollow} className="mt-0.5" />
    </label>}

    {exact && <div className="space-y-2">
      <StartPicker date={date} time={time} onStart={onStart} />
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className={chip} onClick={() => pick(roundUp(new Date(Date.now() + 3600_000)))}><Clock className="mr-1 inline h-3 w-3" />In 1 hour</button>
        {morning && <button type="button" className={chip} onClick={() => pick(morning)}
          title="The next hour when every selected account is awake in its own time"><Sun className="mr-1 inline h-3 w-3" />Accounts’ daytime</button>}
      </div>
      {problem
        ? <p role="alert" className="text-[11px] text-red-300">{problem}</p>
        : start && <p className="text-[11px] leading-relaxed text-zinc-400">
          <span className="text-zinc-200">{yourTime(start)}</span> your time
          {zones.map(zone => <span key={zone}> · {clockIn(zone, start)} {zonePlace(zone)}</span>)}
        </p>}
    </div>}

    {!auto && <div className="space-y-1.5">
      <span className="block text-xs text-zinc-400">Accounts start</span>
      <Select value={String(spread)} onValueChange={value => onSpread(Number(value))} ariaLabel="How far apart accounts start" options={SPREAD_OPTIONS} />
      <p className="text-[11px] text-zinc-500">{spreadHint(spread)} The queue never runs more at once than your resource limits allow.</p>
    </div>}

    {auto && profileIds.length > 0 && <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-2.5 text-[11px]">
      <p className="font-medium text-zinc-300">Planned times</p>
      {!preview && !previewError && <p className="mt-1 text-zinc-500">Planning…</p>}
      <ul className="mt-1 space-y-1">{accounts.slice(0, 8).map(a => {
        const times = (a.planned_at || []).map(t => new Date(t));
        return <li key={a.profile_id} className="text-zinc-400">
          <span className="text-zinc-200">{a.name}</span>
          {a.error ? <span className="text-red-300"> · {a.error}</span> : times.length > 0 && <>
            {' · '}{dayWord(times[0])} {clock(times[0])}
            {a.timezone && <span className="text-zinc-500"> ({clockIn(a.timezone, times[0])} {zonePlace(a.timezone)})</span>}
            {times.length > 1 && <span className="text-zinc-500"> · then {times.slice(1, 4).map(t => `${dayWord(t) === dayWord(times[0]) ? '' : `${dayWord(t)} `}${clock(t)}`).join(', ')}{times.length > 4 ? ` +${times.length - 4}` : ''}</span>}
          </>}
        </li>;
      })}{accounts.length > 8 && <li className="text-zinc-500">and {accounts.length - 8} more</li>}</ul>
      {later.length > 0 && <p className="mt-1.5 text-amber-300">{later.length === 1 ? '1 account has' : `${later.length} accounts have`} posts planned after tomorrow, because of the daily limit or gaps. For urgent news, use Start now or turn off Follow active hours.</p>}
    </div>}

    {previewError && <p className="text-[11px] text-red-300">{previewError}</p>}
    {exact && !problem && atNight.length > 0 && <div role="alert" className="rounded-lg border border-amber-800/60 bg-amber-950/20 p-2.5 text-[11px] text-amber-200">
      <p className="flex items-center gap-1.5 font-medium"><Moon className="h-3 w-3" />Starts at night for {atNight.length === 1 ? 'this account' : `${atNight.length} accounts`}</p>
      <ul className="mt-1 space-y-0.5 text-amber-200/80">{atNight.slice(0, 6).map(a => <li key={a.profile_id}>
        {a.name}: {start ? clockIn(a.timezone, start) : ''} in {zonePlace(a.timezone)}
      </li>)}{atNight.length > 6 && <li>and {atNight.length - 6} more</li>}</ul>
      <p className="mt-1 text-amber-200/70">Posting in an account’s night, day after day, looks automated.{morning ? ` Every account is awake from ${yourTime(morning)}.` : ''}</p>
    </div>}
  </div>;
}
