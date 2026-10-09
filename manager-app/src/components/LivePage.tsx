import { useState } from 'react';
import { Radio } from 'lucide-react';
import { Profile } from '../types/profile';
import { BatchPostCreator } from './BatchPostCreator';
import { PostingQueuePanel } from './PostingQueuePanel';

export function LivePage({ profiles, onEditProfile, onSelectProfile }: {
  profiles: Profile[]; onEditProfile: (id: string) => void; onSelectProfile: (id: string) => void;
}) {
  const [page, setPage] = useState<'create' | 'queue' | 'profiles'>('create');
  return <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
    <header className="border-b border-zinc-800 px-6 py-4">
      <h1 className="flex items-center gap-2 text-lg font-semibold"><Radio className="h-5 w-5 text-rose-400" />Facebook Live</h1>
      <p className="mt-1 text-xs text-zinc-400">Broadcast uploaded videos with saved profile settings. Choose playback, loop duration and schedule.</p>
      <nav aria-label="Live pages" className="mt-4 flex gap-2">
        {([{id: 'create', label: 'New broadcast'}, {id: 'queue', label: 'Live queue'}, {id: 'profiles', label: 'Profile settings'}] as const).map(tab =>
          <button key={tab.id} type="button" aria-current={page === tab.id ? 'page' : undefined} onClick={() => setPage(tab.id)}
            className={`rounded-lg px-3 py-2 text-xs ${page === tab.id ? 'bg-rose-950 text-rose-200' : 'text-zinc-400 hover:bg-zinc-900'}`}>{tab.label}</button>)}
      </nav>
    </header>
    <div className={`flex-1 overflow-y-auto p-6 ${page === 'create' ? '' : 'hidden'}`}>
      <BatchPostCreator mode="live" profiles={profiles} onBatchCreated={() => setPage('queue')} />
    </div>
    <div className={`flex-1 overflow-y-auto p-6 ${page === 'queue' ? '' : 'hidden'}`}>
      <PostingQueuePanel profiles={profiles} postTypeFilter="live" />
    </div>
    {page === 'profiles' && <div className="flex-1 space-y-3 overflow-y-auto p-6">
      <p className="text-xs text-zinc-400">Save a separate streaming key for each destination in Edit profile. Open its browser to prepare Facebook Live Producer before running.</p>
      {profiles.map(profile => <div key={profile.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="min-w-0 flex-1"><p className="truncate text-sm text-zinc-200">{profile.name}</p><p className="text-xs text-zinc-500">{profile.id} · {profile.live?.configured ? 'Streaming settings saved' : 'Live setup required'}</p></div>
        <button type="button" onClick={() => onEditProfile(profile.id)} className="rounded border border-zinc-700 px-3 py-2 text-xs text-zinc-300">Edit profile</button>
        <button type="button" onClick={() => onSelectProfile(profile.id)} className="rounded border border-zinc-700 px-3 py-2 text-xs text-zinc-300">Open browser</button>
      </div>)}
      {!profiles.length && <p className="text-xs text-zinc-500">Create a profile to set up Facebook Live.</p>}
    </div>}
  </section>;
}
