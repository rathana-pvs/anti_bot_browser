import { useState } from 'react';
import { updateExecution } from '../../services/api';
import { QueueExecutionItem } from '../../types/automation';
import { localDate, StartPicker, startFrom, startProblem } from '../BatchSchedule';
import { Button } from '../ui/Button';
import { Dialog } from '../ui/Dialog';
import { Textarea } from '../ui/Textarea';
import { clockIn, zonePlace } from './queueModel';

const pad = (n: number) => String(n).padStart(2, '0');

/** One waiting job: its own start time, or this account's caption. */
export function EditJobDialog({ item, profileName, onClose, onSaved }: {
  item: QueueExecutionItem; profileName: string; onClose: () => void; onSaved: () => void | Promise<void>;
}) {
  const due = new Date(item.expected_start_at || item.scheduled_at);
  const first = Number.isNaN(due.getTime()) || due.getTime() < Date.now() ? new Date(Date.now() + 3600_000) : due;
  first.setMinutes(Math.ceil(first.getMinutes() / 5) * 5, 0, 0);
  const [start, setStart] = useState({ date: localDate(first), time: `${pad(first.getHours())}:${pad(first.getMinutes())}` });
  const [changeTime, setChangeTime] = useState(false);
  const original = item.spun_caption || item.base_caption || '';
  const [caption, setCaption] = useState(original);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lockedCaption = item.post_type === 'live' || item.post_type === 'warming';
  const at = startFrom(start.date, start.time);
  const problem = changeTime ? startProblem(start.date, start.time) : null;
  const changed = changeTime || (!lockedCaption && caption !== original);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await updateExecution(item.execution_id, {
        ...(changeTime && at ? { scheduled_at: at.toISOString() } : {}),
        ...(!lockedCaption && caption !== original ? { caption } : {}),
      });
      await onSaved();
      onClose();
    } catch (err: any) { setError(err.message || 'Could not change this job.'); }
    finally { setSaving(false); }
  };

  return <Dialog open onOpenChange={open => { if (!open && !saving) onClose(); }} title={`Edit ${profileName}'s job`}
    description="Changes only this account's job. It must not have started yet." className="max-w-lg"
    footer={<>
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button onClick={save} loading={saving} disabled={!changed || !!problem}>Save</Button>
    </>}>
    <div className="space-y-4 text-xs">
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="font-medium text-zinc-200">Start time</span>
          <button type="button" className="text-blue-400 hover:text-blue-300" onClick={() => setChangeTime(!changeTime)}>{changeTime ? 'Keep current time' : 'Change time'}</button>
        </div>
        {changeTime ? <>
          <StartPicker date={start.date} time={start.time} onStart={(date, time) => setStart({ date, time })} />
          {problem ? <p role="alert" className="text-[11px] text-red-300">{problem}</p>
            : at && item.account_timezone && <p className="text-[11px] text-zinc-400">{clockIn(item.account_timezone, at)} in {zonePlace(item.account_timezone)}</p>}
          <p className="text-[11px] text-zinc-500">Active hours, the account's other posts and its daily limit still apply, so it may start a little later.</p>
        </> : <p className="text-zinc-400">{Number.isNaN(due.getTime()) ? 'Not set' : due.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</p>}
      </div>
      <label className="block space-y-1"><span className="font-medium text-zinc-200">Caption for this account</span>
        {lockedCaption ? <p className="text-zinc-500">This job has no caption to edit here.</p>
          : <Textarea rows={4} value={caption} onChange={e => setCaption(e.target.value)} />}
      </label>
      {error && <p role="alert" className="text-red-400">{error}</p>}
    </div>
  </Dialog>;
}
