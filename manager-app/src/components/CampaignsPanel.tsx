import React, { useState, useCallback, useEffect } from 'react';
import { Profile } from '../types/profile';
import { BatchPostCreator, CampaignKind } from './BatchPostCreator';
import { ReelCreator } from './reel-creator/ReelCreator';
import { ReelCreatorBoundary } from './reel-creator/ReelCreatorBoundary';
import type { CreatedReelMedia } from '../types/reelCreator';
import { PostingQueuePanel } from './PostingQueuePanel';
import { cn } from './ui/cn';
import { Clock, Film, Flame, Plus, Radio, Send } from 'lucide-react';

interface CampaignsPanelProps {
  profiles: Profile[];
  onSelectProfile: (profileId: string) => void;
  onEditProfile: (profileId: string) => void;
  onOpenAccount?: (profileId: string) => void;
  onAttentionChange?: (count: number) => void;
  /** Set by other pages to bring the Queue tab forward, optionally showing one account. */
  queueRequest?: { profileId?: string; key: number } | null;
}

type CampaignTab = 'queue' | 'create' | 'reel_studio';

const KINDS: { value: CampaignKind; label: string; description: string; icon: React.ReactNode }[] = [
  { value: 'posts', label: 'Posts and reels', description: 'Photos, videos, Reels, and text', icon: <Send className="h-4 w-4" /> },
  { value: 'live', label: 'Live', description: 'Broadcast a video', icon: <Radio className="h-4 w-4" /> },
  { value: 'warmup', label: 'Warm-up only', description: 'Browse without posting', icon: <Flame className="h-4 w-4" /> },
];

export const CampaignsPanel: React.FC<CampaignsPanelProps> = ({
  profiles,
  onSelectProfile,
  onEditProfile,
  onOpenAccount,
  onAttentionChange,
  queueRequest = null,
}) => {
  const [tab, setTab] = useState<CampaignTab>('queue');
  const [kind, setKind] = useState<CampaignKind>(() => {
    const saved = localStorage.getItem('campaign_kind');
    return saved === 'live' || saved === 'warmup' ? saved : 'posts';
  });
  const [attention, setAttention] = useState(0);
  useEffect(() => { onAttentionChange?.(attention); }, [attention, onAttentionChange]);
  useEffect(() => { if (queueRequest) setTab('queue'); }, [queueRequest?.key]);
  const [incomingReel, setIncomingReel] = useState<CreatedReelMedia | null>(null);
  const consumeIncomingReel = useCallback(() => setIncomingReel(null), []);
  const chooseKind = (next: CampaignKind) => {
    setKind(next);
    try { localStorage.setItem('campaign_kind', next); } catch (_) {}
  };
  const useCreatedReel = useCallback((media: CreatedReelMedia) => {
    setIncomingReel(media);
    setKind(media.postType === 'live' ? 'live' : 'posts');
    setTab('create');
  }, []);

  const tabs: { value: CampaignTab; label: string; icon: React.ReactNode; badge?: number }[] = [
    { value: 'queue', label: 'Queue', icon: <Clock className="h-3.5 w-3.5" />, badge: attention },
    { value: 'create', label: 'Create', icon: <Plus className="h-3.5 w-3.5" /> },
    { value: 'reel_studio', label: 'Reel Studio', icon: <Film className="h-3.5 w-3.5" /> },
  ];

  return (
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden">
      <header className="flex shrink-0 items-end gap-4 border-b border-border bg-surface/50 px-6 pt-3">
        <div className="flex items-center gap-2 pb-2.5">
          <h2 className="text-sm font-semibold text-zinc-100">Campaigns</h2>
        </div>
        <nav role="tablist" aria-label="Campaign sections" className="flex items-end gap-1 overflow-x-auto">
          {tabs.map(option => <button key={option.value} type="button" role="tab" aria-selected={tab === option.value}
            onClick={() => setTab(option.value)}
            className={cn('flex shrink-0 items-center gap-2 border-b-2 px-3 pb-2.5 pt-1 text-xs font-semibold transition-colors',
              tab === option.value ? 'border-blue-500 text-white' : 'border-transparent text-zinc-400 hover:text-zinc-200')}>
            {option.icon}{option.label}
            {!!option.badge && <span className="rounded-full bg-amber-500/20 px-1.5 text-[10px] text-amber-300" aria-label={`${option.badge} need attention`}>{option.badge}</span>}
          </button>)}
        </nav>
      </header>

      <div className={`min-h-0 flex-1 overflow-hidden p-6 ${tab === 'queue' ? '' : 'hidden'}`}>
        <PostingQueuePanel profiles={profiles} onAttentionChange={setAttention}
          onWatchProfile={onSelectProfile} onOpenAccount={onOpenAccount} onCreate={() => setTab('create')}
          focusAccount={queueRequest?.profileId ? { profileId: queueRequest.profileId, key: queueRequest.key } : null} />
      </div>

      <div className={`flex-1 overflow-y-auto p-6 ${tab === 'create' ? '' : 'hidden'}`}>
        <div className="mx-auto mb-4 max-w-7xl">
          <div role="radiogroup" aria-label="What to run" className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {KINDS.map(option => <button key={option.value} type="button" role="radio" aria-checked={kind === option.value}
              onClick={() => chooseKind(option.value)}
              className={cn('flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors',
                kind === option.value ? 'border-blue-600 bg-blue-950/30 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200')}>
              <span className={kind === option.value ? 'text-blue-300' : 'text-zinc-500'}>{option.icon}</span>
              <span><span className="block text-xs font-semibold">{option.label}</span><span className="block text-[11px] text-zinc-500">{option.description}</span></span>
            </button>)}
          </div>
        </div>
        {/* Each kind keeps its own saved draft and settings, so remount on change. */}
        <BatchPostCreator key={kind} mode={kind} profiles={profiles} onEditProfile={onEditProfile}
          onBatchCreated={() => setTab('queue')}
          incomingReel={kind === (incomingReel?.postType === 'live' ? 'live' : 'posts') ? incomingReel : null} onIncomingReelConsumed={consumeIncomingReel} />
      </div>

      <div className={`min-h-0 flex-1 overflow-y-auto ${tab === 'reel_studio' ? '' : 'hidden'}`}>
        <ReelCreatorBoundary><ReelCreator active={tab === 'reel_studio'} onUseReel={useCreatedReel} /></ReelCreatorBoundary>
      </div>
    </div>
  );
};
