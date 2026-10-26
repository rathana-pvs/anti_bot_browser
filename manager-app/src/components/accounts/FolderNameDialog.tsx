import { useEffect, useState } from 'react';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { Dialog } from '../ui/Dialog';
import { Input } from '../ui/Input';

export type FolderDialogState = { mode: 'create' } | { mode: 'rename'; name: string } | null;

/** Name prompt for creating or renaming a folder. */
export function FolderNameDialog({ state, onSubmit, onClose, resource = 'accounts' }: {
  state: FolderDialogState; onSubmit: (name: string) => Promise<void>; onClose: () => void;
  resource?: 'accounts' | 'proxies';
}) {
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setName(state?.mode === 'rename' ? state.name : ''); setError(''); }, [state]);
  if (!state) return null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true); setError('');
    try { await onSubmit(name.trim()); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Couldn’t save this folder.'); }
    finally { setSaving(false); }
  };

  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}
    title={state.mode === 'create' ? 'New folder' : `Rename “${state.name}”`}
    description={resource === 'proxies' ? (state.mode === 'create' ? 'Create a folder, then move proxies into it.' : 'Proxies in this folder stay in it.') : state.mode === 'create' ? 'Folders organize accounts here and in campaign account pickers.' : 'Accounts in this folder stay in it.'}
    footer={<>
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button type="submit" form="folder-name-form" loading={saving} disabled={!name.trim()}>{state.mode === 'create' ? 'Create folder' : 'Rename'}</Button>
    </>}>
    <form id="folder-name-form" onSubmit={submit} className="space-y-3">
      <Input autoFocus required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder={resource === 'proxies' ? 'Cambodia residential' : 'Cambodia food pages'} aria-label="Folder name" />
      {error && <Alert variant="danger">{error}</Alert>}
    </form>
  </Dialog>;
}
