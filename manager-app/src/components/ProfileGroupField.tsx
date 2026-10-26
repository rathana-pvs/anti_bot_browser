import { Select } from './ui/Select';

export function ProfileGroupField({ value, onChange, groups }: {
  value: string; onChange: (value: string) => void; groups: string[];
}) {
  return (
    <label className="block text-xs text-zinc-400">Folder
      <Select value={value ? `group:${value}` : '__none__'} onValueChange={(next) => onChange(next === '__none__' ? '' : next.slice(6))}
        ariaLabel="Folder" className="mt-1.5"
        options={[{ value: '__none__', label: 'No folder' }, ...groups.map((group) => ({ value: `group:${group}`, label: group }))]} />
      <span className="mt-1 block text-[11px] text-zinc-500">Create folders with the + button in the Folders column on the Accounts page.</span>
    </label>
  );
}
