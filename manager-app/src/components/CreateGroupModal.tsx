import { useState } from 'react';
import { FolderPlus, Loader2, X } from 'lucide-react';

export function CreateGroupModal({ onCreate, onClose }: {
  onCreate: (name: string) => Promise<void>; onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
    <form role="dialog" aria-modal="true" aria-label="Create profile group" onSubmit={async (event) => {
      event.preventDefault(); setSaving(true); setError('');
      try { await onCreate(name.trim()); onClose(); }
      catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create group'); }
      finally { setSaving(false); }
    }} className="w-full max-w-sm space-y-4 rounded-2xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
      <div className="flex items-center gap-2"><FolderPlus className="h-4 w-4 text-blue-400" />
        <h2 className="flex-1 text-sm font-semibold text-white">New profile group</h2>
        <button type="button" onClick={onClose} disabled={saving} aria-label="Close group dialog"><X className="h-4 w-4 text-zinc-400" /></button>
      </div>
      <p className="text-xs text-zinc-400">Organize accounts by brand, team, or campaign. You can add profiles to this group later.</p>
      <label className="block text-xs text-zinc-400">Group name
        <input autoFocus required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Cambodia · Travel"
          className="mt-2 w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-white focus:border-blue-500 focus:outline-none" />
      </label>
      {error && <p role="alert" className="text-xs text-red-400">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" disabled={saving} onClick={onClose} className="rounded-lg px-3 py-2 text-xs text-zinc-400">Cancel</button>
        <button type="submit" disabled={saving || !name.trim()} className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-40">
          {saving && <Loader2 className="h-3 w-3 animate-spin" />}Create group
        </button>
      </div>
    </form>
  </div>;
}
