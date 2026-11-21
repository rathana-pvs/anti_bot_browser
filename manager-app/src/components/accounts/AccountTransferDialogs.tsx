import { useRef, useState } from 'react';
import { FileUp } from 'lucide-react';
import { exportAccount, importAccount } from '../../services/api';
import { Profile } from '../../types/profile';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Dialog } from '../ui/Dialog';
import { Field } from '../ui/Field';
import { Input } from '../ui/Input';

const MIN_PASSWORD = 8;
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

/** Saves a stopped PC account as one .abprofile file for another PC. */
export function ExportAccountDialog({ profile, onClose, onExported }: {
  profile: Profile; onClose: () => void; onExported: (message: string) => void;
}) {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [hold, setHold] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;
  const mismatch = password.length > 0 && confirm !== password;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (tooShort || mismatch) return;
    setSaving(true); setError('');
    try {
      const result = await exportAccount(profile.id, { password: password || undefined, hold });
      onExported(`Exported ${profile.name} to ${result.location}.${result.onHold ? ' It is on hold on this PC.' : ''}`);
      onClose();
    } catch (reason) {
      setError(message(reason, 'Couldn’t export this account.'));
    } finally {
      setSaving(false);
    }
  };

  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}
    title={`Export ${profile.name}`}
    description="Saves the account’s login, fingerprint and proxy in one file. Import it on the other PC from the Accounts page."
    footer={<>
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button type="submit" form="export-account-form" loading={saving} disabled={tooShort || mismatch}>Export</Button>
    </>}>
    <form id="export-account-form" onSubmit={submit} className="space-y-4">
      <Field label="Password (optional)" htmlFor="export-password"
        description="Encrypts the file. It holds a live Facebook login and the proxy password, so set one if the file leaves your hands."
        error={tooShort ? `Use at least ${MIN_PASSWORD} characters.` : undefined}>
        <Input id="export-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      {password.length > 0 && <Field label="Repeat password" htmlFor="export-password-confirm" error={confirm && mismatch ? 'The passwords differ.' : undefined}>
        <Input id="export-password-confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </Field>}
      <label htmlFor="export-hold" className="flex items-start gap-2.5 text-xs text-zinc-300">
        <Checkbox id="export-hold" checked={hold} onCheckedChange={setHold} className="mt-0.5" />
        <span>Put this account on hold here<span className="block text-[11px] text-zinc-500">Nothing posts or warms it on this PC. Running one login on two PCs at once can get it flagged.</span></span>
      </label>
      {error && <Alert variant="danger">{error}</Alert>}
    </form>
  </Dialog>;
}

/** Creates a new account from a file exported on another PC. */
export function ImportAccountDialog({ onClose, onImported }: {
  onClose: () => void; onImported: (profile: Profile) => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setSaving(true); setError('');
    try {
      onImported(await importAccount(file, password));
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
      <Field label="Password" htmlFor="import-password" description="Only for a file exported with a password.">
        <Input id="import-password" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Alert variant="info">Use the file on one PC only. After importing, delete the account on the old PC, or keep it on hold.</Alert>
      {error && <Alert variant="danger">{error}</Alert>}
    </form>
  </Dialog>;
}
