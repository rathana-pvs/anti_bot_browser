import { useEffect, useState } from 'react';
import { Profile } from '../../types/profile';
import { Button } from '../ui/Button';
import { Dialog } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { Select } from '../ui/Select';

const NONE = '__none__';
const NEW = '__new__';

export function MoveToGroupDialog({ profiles, groups, onMove, onClose }: {
  profiles: Profile[] | null; groups: string[];
  onMove: (profiles: Profile[], group: string, isNew: boolean) => Promise<void>; onClose: () => void;
}) {
  const [choice, setChoice] = useState(NONE);
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!profiles) return;
    const current = profiles.every(p => (p.group || '') === (profiles[0].group || '')) ? profiles[0].group || '' : '';
    setChoice(current ? `group:${current}` : NONE);
    setNewName('');
  }, [profiles]);
  if (!profiles) return null;

  const target = choice === NEW ? newName.trim() : choice === NONE ? '' : choice.slice(6);
  const submit = async () => {
    if (choice === NEW && !target) return;
    setSaving(true);
    try { await onMove(profiles, target, choice === NEW); onClose(); } finally { setSaving(false); }
  };

  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}
    title={profiles.length === 1 ? `Move ${profiles[0].name}` : `Move ${profiles.length} accounts`}
    description="Folders organize accounts here and in campaign account pickers."
    footer={<>
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button onClick={submit} loading={saving} disabled={choice === NEW && !target}>Move</Button>
    </>}>
    <div className="space-y-3">
      <Select value={choice} onValueChange={setChoice} ariaLabel="Folder"
        options={[{ value: NONE, label: 'No folder' }, ...groups.map(group => ({ value: `group:${group}`, label: group })), { value: NEW, label: 'New folder…' }]} />
      {choice === NEW && <Input autoFocus maxLength={80} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Cambodia food pages" aria-label="New folder name" />}
    </div>
  </Dialog>;
}
