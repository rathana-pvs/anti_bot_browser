import { useState } from 'react';
import { FolderPlus } from 'lucide-react';
import { Alert } from './ui/Alert';
import { Button } from './ui/Button';
import { Dialog } from './ui/Dialog';
import { Field } from './ui/Field';
import { Input } from './ui/Input';
import { Spinner } from './ui/Spinner';

export function CreateGroupModal({ onCreate, onClose }: {
  onCreate: (name: string) => Promise<void>; onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  return <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}
    title="New profile group"
    description="Organize accounts by brand, team, or campaign. You can add profiles to this group later."
    footer={<>
      <Button type="button" variant="ghost" disabled={saving} onClick={onClose}>Cancel</Button>
      <Button type="submit" form="create-profile-group" disabled={saving || !name.trim()} loading={saving}>
        {saving && <Spinner label="Creating group" className="h-3.5 w-3.5" />}Create group
      </Button>
    </>}>
    <form id="create-profile-group" onSubmit={async (event) => {
      event.preventDefault(); setSaving(true); setError('');
      try { await onCreate(name.trim()); onClose(); }
      catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create group'); }
      finally { setSaving(false); }
    }} className="space-y-4">
      <div className="flex items-center gap-2 text-xs text-zinc-400"><FolderPlus className="h-4 w-4 text-blue-400" />Groups are visible in profile selectors.</div>
      <Field label="Group name" htmlFor="profile-group-name">
        <Input id="profile-group-name" autoFocus required maxLength={80} value={name}
          onChange={(e) => setName(e.target.value)} placeholder="e.g. Cambodia · Travel" />
      </Field>
      {error && <Alert variant="danger">{error}</Alert>}
    </form>
  </Dialog>;
}
