import { useState } from 'react';
import { Radio } from 'lucide-react';
import { Profile } from '../types/profile';
import { BatchPostCreator } from './BatchPostCreator';
import { PostingQueuePanel } from './PostingQueuePanel';
import { Button } from './ui/Button';
import { Card } from './ui/Card';
import { EmptyState } from './ui/EmptyState';
import { Tabs } from './ui/Tabs';

export function LivePage({ profiles, onEditProfile, onSelectProfile }: {
  profiles: Profile[]; onEditProfile: (id: string) => void; onSelectProfile: (id: string) => void;
}) {
  const [page, setPage] = useState<'create' | 'queue' | 'profiles'>('create');
  return <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
    <header className="border-b border-zinc-800 px-6 py-4">
      <h1 className="flex items-center gap-2 text-lg font-semibold"><Radio className="h-5 w-5 text-rose-400" />Facebook Live</h1>
      <p className="mt-1 text-xs text-zinc-400">Broadcast uploaded videos with saved profile settings. Choose playback, loop duration and schedule.</p>
      <div className="mt-4"><Tabs value={page} onValueChange={setPage} ariaLabel="Live pages" tone="rose" options={[
        {value: 'create', label: 'New broadcast'}, {value: 'queue', label: 'Live queue'}, {value: 'profiles', label: 'Profile settings'},
      ]} /></div>
    </header>
    <div className={`flex-1 overflow-y-auto p-6 ${page === 'create' ? '' : 'hidden'}`}>
      <BatchPostCreator mode="live" profiles={profiles} onBatchCreated={() => setPage('queue')} />
    </div>
    <div className={`flex-1 overflow-y-auto p-6 ${page === 'queue' ? '' : 'hidden'}`}>
      <PostingQueuePanel profiles={profiles} postTypeFilter="live" />
    </div>
    {page === 'profiles' && <div className="flex-1 space-y-3 overflow-y-auto p-6">
      <p className="text-xs text-zinc-400">Save a separate streaming key for each destination in Edit profile. Open its browser to prepare Facebook Live Producer before running.</p>
      {profiles.map(profile => <Card key={profile.id} className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1"><p className="truncate text-sm text-zinc-200">{profile.name}</p><p className="text-xs text-zinc-500">{profile.id} · {profile.live?.configured ? 'Streaming settings saved' : 'Live setup required'}</p></div>
        <Button variant="outline" onClick={() => onEditProfile(profile.id)}>Edit profile</Button>
        <Button variant="outline" onClick={() => onSelectProfile(profile.id)}>Open browser</Button>
      </Card>)}
      {!profiles.length && <EmptyState title="No profiles yet" description="Create a profile to set up Facebook Live." />}
    </div>}
  </section>;
}
