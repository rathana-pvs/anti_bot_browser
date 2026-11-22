import { useRef, useState } from 'react';
import { FileUp } from 'lucide-react';
import { exportAccount, importAccount } from '../../services/api';
import { Profile } from '../../types/profile';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Dialog } from '../ui/Dialog';

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

/** The folder a saved file went to, or the browser's downloads when only a file name is known. */
const folderOf = (location: string) => /[\\/]/.test(location) ? location.replace(/[\\/][^\\/]*$/, '') : 'your downloads';

/** Saves stopped PC accounts as one .abprofile file each for another PC, one after another. */
export function ExportAccountDialog({ profiles, onClose, onExported }: {
  profiles: Profile[]; onClose: () => void; onExported: (message: string) => void;
}) {
  const [hold, setHold] = useState(true);
  const [saving, setSaving] = useState(false);
  const [progress, setProgress] = useState('');
  const [failures, setFailures] = useState<{ name: string; reason: string }[]>([]);
  const single = profiles.length === 1;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true); setFailures([]);
    const saved: { name: string; location: string }[] = [];
    const failed: { name: string; reason: string }[] = [];
    let onHold = false;
    for (const [index, profile] of profiles.entries()) {
      if (!single) setProgress(`Exporting ${index + 1} of ${profiles.length} · ${profile.name}`);
      try {
        const result = await exportAccount(profile.id, { hold });
        saved.push({ name: profile.name, location: result.location });
        onHold = onHold || result.onHold;
      } catch (reason) {
        failed.push({ name: profile.name, reason: message(reason, 'Couldn’t export this account.') });
      }
    }
    setSaving(false); setProgress('');
    if (saved.length) {
      const held = onHold ? (saved.length === 1 ? ' It is on hold on this PC.' : ' They are on hold on this PC.') : '';
      onExported(saved.length === 1
        ? `Exported ${saved[0].name} to ${saved[0].location}.${held}`
        : `Exported ${saved.length} accounts to ${folderOf(saved[0].location)}.${held}`);
    }
    if (failed.length) setFailures(failed);
    else onClose();
  };

  const done = failures.length > 0 && !saving;
  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}
    title={single ? `Export ${profiles[0].name}` : `Export ${profiles.length} accounts`}
    description={single
      ? 'Saves the account’s login, fingerprint and proxy in one file. Import it on the other PC from the Accounts page.'
      : 'Saves each account’s login, fingerprint and proxy in its own file. Import them on the other PC from the Accounts page.'}
    footer={done
      ? <Button onClick={onClose}>Close</Button>
      : <>
        <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
        <Button type="submit" form="export-account-form" loading={saving}>Export</Button>
      </>}>
    <form id="export-account-form" onSubmit={submit} className="space-y-4">
      <label htmlFor="export-hold" className="flex items-start gap-2.5 text-xs text-zinc-300">
        <Checkbox id="export-hold" checked={hold} onCheckedChange={setHold} disabled={saving || done} className="mt-0.5" />
        <span>{single ? 'Put this account on hold here' : 'Put these accounts on hold here'}<span className="block text-[11px] text-zinc-500">Nothing posts or warms {single ? 'it' : 'them'} on this PC. Running one login on two PCs at once can get it flagged.</span></span>
      </label>
      <Alert variant="info">The file holds a live Facebook login and the proxy password. Keep it to yourself.</Alert>
      {progress && <p className="text-xs text-zinc-400" aria-live="polite">{progress}</p>}
      {failures.length > 0 && <Alert variant="danger">
        <p>{failures.length === profiles.length ? 'Nothing was exported.' : `${failures.length} of ${profiles.length} were not exported:`}</p>
        <ul className="mt-1 space-y-0.5">{failures.map(f => <li key={f.name}><span className="font-medium">{f.name}</span>: {f.reason}</li>)}</ul>
      </Alert>}
    </form>
  </Dialog>;
}

/** Creates a new account from a file exported on another PC. */
export function ImportAccountDialog({ onClose, onImported }: {
  onClose: () => void; onImported: (profile: Profile) => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setSaving(true); setError('');
    try {
      onImported(await importAccount(file));
      onClose();
    } catch (reason) {
      setError(message(reason, 'Couldn’t import this account.'));
    } finally {
      setSaving(false);
    }
  };

  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}
    title="Import account"
    description="Adds an account exported on another PC. It keeps its login, fingerprint and proxy, and gets a new ID here."
    footer={<>
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button type="submit" form="import-account-form" loading={saving} disabled={!file}>Import</Button>
    </>}>
    <form id="import-account-form" onSubmit={submit} className="space-y-4">
      <input ref={picker} type="file" accept=".abprofile,.zip" aria-label="Account export file" className="hidden"
        onChange={(event) => { setFile(event.target.files?.[0] ?? null); setError(''); event.target.value = ''; }} />
      <Button type="button" variant="outline" className="w-full justify-start" onClick={() => picker.current?.click()} disabled={saving}>
        <FileUp className="h-3.5 w-3.5" />
        <span className="truncate">{file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB` : 'Choose .abprofile file…'}</span>
      </Button>
      <Alert variant="info">Use the file on one PC only. After importing, delete the account on the old PC, or keep it on hold.</Alert>
      {error && <Alert variant="danger">{error}</Alert>}
    </form>
  </Dialog>;
}
