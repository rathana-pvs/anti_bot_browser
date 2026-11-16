import { Users } from 'lucide-react';
import type { Profile } from '../../types/profile';
import { accountLimit, proxyHolders, proxyOfProfile, ProxyItem } from '../../types/proxy';
import { cn } from '../ui/cn';

/** How many accounts share this account's proxy, e.g. "2/3". Hidden while proxies are not shared.
 * With `onFilter` it is a button that shows only the accounts on this proxy. */
export function ProxyShareBadge({ profile, proxies, profiles, className, active, onFilter }: {
  profile: Profile; proxies: ProxyItem[]; profiles?: Profile[]; className?: string;
  /** The list is already filtered to this proxy. */
  active?: boolean;
  onFilter?: (proxyId: string) => void;
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
  const look = cn('inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium',
    holders.length > limit ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
      : full ? 'border-zinc-600 bg-zinc-800 text-zinc-300' : 'border-sky-500/30 bg-sky-500/10 text-sky-300', className);
  const content = <><Users className="h-3 w-3" aria-hidden="true" />{holders.length}/{limit}</>;
  if (!onFilter) return <span title={title} aria-label={title} className={look}>{content}</span>;
  const action = active ? ' Click to show all accounts again.' : ' Click to show only these accounts.';
  return <button type="button" title={title + action} aria-label={title + action} aria-pressed={!!active}
    onClick={(event) => { event.stopPropagation(); onFilter(proxy.id); }}
    className={cn(look, 'cursor-pointer transition-colors hover:brightness-125 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
      active && 'ring-1 ring-blue-500')}>{content}</button>;
}
