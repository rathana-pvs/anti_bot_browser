import { Users } from 'lucide-react';
import type { Profile } from '../../types/profile';
import { accountLimit, proxyHolders, proxyOfProfile, ProxyItem } from '../../types/proxy';
import { cn } from '../ui/cn';

/** How many accounts share this account's proxy, e.g. "2/3". Hidden while proxies are not shared. */
export function ProxyShareBadge({ profile, proxies, profiles, className }: {
  profile: Profile; proxies: ProxyItem[]; profiles?: Profile[]; className?: string;
}) {
  const proxy = proxyOfProfile(profile, proxies);
  if (!proxy) return null;
  const holders = proxyHolders(proxy);
  const limit = accountLimit(proxy);
  if (limit === 1 && holders.length <= 1) return null;
  const others = holders.filter(id => id !== profile.id).map(id => profiles?.find(p => p.id === id)?.name || id);
  const full = holders.length >= limit;
  const title = `${holders.length} of ${limit} accounts use ${proxy.host}:${proxy.port}.` +
    (others.length ? ` Also used by: ${others.join(', ')}.` : ' No other account uses it yet.');
  return <span title={title} aria-label={title}
    className={cn('inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium',
      holders.length > limit ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
        : full ? 'border-zinc-600 bg-zinc-800 text-zinc-300' : 'border-sky-500/30 bg-sky-500/10 text-sky-300', className)}>
    <Users className="h-3 w-3" aria-hidden="true" />{holders.length}/{limit}
  </span>;
}
