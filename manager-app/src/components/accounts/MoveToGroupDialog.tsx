import { useEffect, useState } from 'react';
import { Profile } from '../../types/profile';
import { Button } from '../ui/Button';
import { Dialog } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { Select } from '../ui/Select';
import { Alert } from '../ui/Alert';

const NONE = '__none__';
const NEW = '__new__';

export function MoveToGroupDialog<T extends Pick<Profile, 'id' | 'name' | 'group'>>({ profiles, groups, onMove, onClose, resource = 'accounts' }: {
  profiles: T[] | null; groups: string[];
  onMove: (profiles: T[], group: string, isNew: boolean) => Promise<void>; onClose: () => void;
  resource?: 'accounts' | 'proxies';
}) {
  const [choice, setChoice] = useState(NONE);
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!profiles) return;
    const current = profiles.every(p => (p.group || '') === (profiles[0].group || '')) ? profiles[0].group || '' : '';
    setChoice(current ? `group:${current}` : NONE);
    setNewName('');
    setError('');
  }, [profiles]);
  if (!profiles) return null;

  const target = choice === NEW ? newName.trim() : choice === NONE ? '' : choice.slice(6);
  const submit = async () => {
    if (choice === NEW && !target) return;
    setSaving(true); setError('');
    try { await onMove(profiles, target, choice === NEW); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not move to folder.'); }
    finally { setSaving(false); }
  };

  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}
    title={profiles.length === 1 ? `Move ${profiles[0].name}` : `Move ${profiles.length} ${resource}`}
    description={resource === 'proxies' ? 'Choose a folder or create a new one for these proxies.' : 'Folders organize accounts here and in campaign account pickers.'}
    footer={<>
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button onClick={submit} loading={saving} disabled={choice === NEW && !target}>Move</Button>
    </>}>
    <div className="space-y-3">
      <Select value={choice} onValueChange={setChoice} ariaLabel="Folder" disabled={saving} contentClassName="z-[400]"
        options={[{ value: NONE, label: 'No folder' }, ...groups.map(group => ({ value: `group:${group}`, label: group })), { value: NEW, label: 'New folder…' }]} />
      {choice === NEW && <Input autoFocus disabled={saving} maxLength={80} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={resource === 'proxies' ? 'Cambodia residential' : 'Cambodia food pages'} aria-label="New folder name" />}
      {error && <Alert variant="danger">{error}</Alert>}
    </div>
  </Dialog>;
}
