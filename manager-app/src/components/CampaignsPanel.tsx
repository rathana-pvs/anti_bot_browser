import React, { useState, useCallback, useEffect } from 'react';
import { Profile } from '../types/profile';
import { BatchPostCreator, CampaignKind } from './BatchPostCreator';
import type { CreatedReelMedia } from '../types/reelCreator';
import { PostingQueuePanel } from './PostingQueuePanel';
import { cn } from './ui/cn';
import { Flame, Radio, Send } from 'lucide-react';

export type PostingSection = 'post' | 'queue';

interface CampaignsPanelProps {
  /** Post makes new work; Queue shows it running. Each has its own place in the left bar. */
  section: PostingSection;
  onSectionChange: (section: PostingSection) => void;
  profiles: Profile[];
  onSelectProfile: (profileId: string) => void;
  onEditProfile: (profileId: string) => void;
  onOpenAccount?: (profileId: string) => void;
  onAttentionChange?: (count: number) => void;
  /** Set by other pages to bring Queue forward, optionally showing one account. */
  queueRequest?: { profileId?: string; executionId?: string; key: number } | null;
  /** A photo, Reel, or Live video sent from Reel Studio, to start a campaign with. */
  reelRequest?: { media: CreatedReelMedia; key: number } | null;
}

// Warm-up is not a kind of post, and Auto warming usually covers it, so it sits below these.
const KINDS: { value: CampaignKind; label: string; description: string; icon: React.ReactNode }[] = [
  { value: 'posts', label: 'Posts and reels', description: 'Photos, videos, Reels, and text', icon: <Send className="h-4 w-4" /> },
  { value: 'live', label: 'Live', description: 'Broadcast a video', icon: <Radio className="h-4 w-4" /> },
];

export const CampaignsPanel: React.FC<CampaignsPanelProps> = ({
  section,
  onSectionChange,
  profiles,
  onSelectProfile,
  onEditProfile,
  onOpenAccount,
  onAttentionChange,
  queueRequest = null,
  reelRequest = null,
}) => {
  const [kind, setKind] = useState<CampaignKind>(() => {
    const saved = localStorage.getItem('campaign_kind');
    return saved === 'live' || saved === 'warmup' ? saved : 'posts';
  });
  const [attention, setAttention] = useState(0);
  useEffect(() => { onAttentionChange?.(attention); }, [attention, onAttentionChange]);
  const [incomingReel, setIncomingReel] = useState<CreatedReelMedia | null>(null);
  const consumeIncomingReel = useCallback(() => setIncomingReel(null), []);
  const chooseKind = (next: CampaignKind) => {
    setKind(next);
    try { localStorage.setItem('campaign_kind', next); } catch (_) {}
  };
  useEffect(() => {
    if (!reelRequest) return;
    setIncomingReel(reelRequest.media);
    setKind(reelRequest.media.postType === 'live' ? 'live' : 'posts');
  }, [reelRequest?.key]);

  return (
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden">
      <div className={`min-h-0 flex-1 overflow-hidden p-6 ${section === 'queue' ? '' : 'hidden'}`}>
        <PostingQueuePanel profiles={profiles} onAttentionChange={setAttention}
          onWatchProfile={onSelectProfile} onOpenAccount={onOpenAccount} onCreate={() => onSectionChange('post')}
          focusAccount={queueRequest?.profileId ? { profileId: queueRequest.profileId, executionId: queueRequest.executionId, key: queueRequest.key } : null} />
      </div>

      <div className={`flex-1 overflow-y-auto p-6 ${section === 'post' ? '' : 'hidden'}`}>
        <div className="mx-auto mb-4 max-w-7xl">
          <div role="radiogroup" aria-label="What to run" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {KINDS.map(option => <button key={option.value} type="button" role="radio" aria-checked={kind === option.value}
              onClick={() => chooseKind(option.value)}
              className={cn('flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors',
                kind === option.value ? 'border-blue-600 bg-blue-950/30 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200')}>
              <span className={kind === option.value ? 'text-blue-300' : 'text-zinc-500'}>{option.icon}</span>
              <span><span className="block text-xs font-semibold">{option.label}</span><span className="block text-[11px] text-zinc-500">{option.description}</span></span>
            </button>)}
          </div>
          <button type="button" onClick={() => chooseKind(kind === 'warmup' ? 'posts' : 'warmup')} aria-pressed={kind === 'warmup'}
            className={cn('mt-2 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] transition-colors',
              kind === 'warmup' ? 'bg-amber-950/40 text-amber-200 hover:text-amber-100' : 'text-zinc-500 hover:text-zinc-300')}>
            <Flame className="h-3.5 w-3.5" />
            {kind === 'warmup' ? 'Warming up without posting · back to posts' : 'Warm up accounts without posting'}
          </button>
        </div>
        {/* Each kind keeps its own saved draft and settings, so remount on change. */}
        <BatchPostCreator key={kind} mode={kind} profiles={profiles} onEditProfile={onEditProfile}
          onBatchCreated={() => onSectionChange('queue')}
          incomingReel={kind === (incomingReel?.postType === 'live' ? 'live' : 'posts') ? incomingReel : null} onIncomingReelConsumed={consumeIncomingReel} />
      </div>
    </div>
  );
};
