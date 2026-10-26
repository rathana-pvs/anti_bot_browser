import { ReactNode } from 'react';
import { Globe, Send, Settings, Users } from 'lucide-react';
import { cn } from './ui/cn';

export type AppPage = 'accounts' | 'campaigns' | 'proxies';

const ITEMS: { value: AppPage; label: string; icon: ReactNode }[] = [
  { value: 'accounts', label: 'Accounts', icon: <Users className="h-5 w-5" /> },
  { value: 'campaigns', label: 'Campaigns', icon: <Send className="h-5 w-5" /> },
  { value: 'proxies', label: 'Proxies', icon: <Globe className="h-5 w-5" /> },
];

/** App-wide navigation. Badges show counts that need the user on that page. */
export function NavRail({ page, onPageChange, onOpenSettings, badges = {} }: {
  page: AppPage; onPageChange: (page: AppPage) => void; onOpenSettings: () => void;
  badges?: Partial<Record<AppPage, number>>;
}) {
  const item = (label: string, icon: ReactNode, active: boolean, onClick: () => void, badge?: number) => (
    <button key={label} type="button" onClick={onClick} aria-current={active ? 'page' : undefined}
      className={cn('relative flex w-14 flex-col items-center gap-1 rounded-xl py-2 text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
        active ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200')}>
      {icon}{label}
      {!!badge && <span className="absolute right-1.5 top-1 min-w-4 rounded-full bg-amber-500 px-1 text-[9px] leading-4 text-zinc-950" aria-label={`${badge} need attention`}>{badge}</span>}
    </button>
  );
  return <nav aria-label="Main" className="flex w-[4.5rem] shrink-0 flex-col items-center gap-1 border-r border-border bg-surface py-3 select-none">
    {ITEMS.map(entry => item(entry.label, entry.icon, page === entry.value, () => onPageChange(entry.value), badges[entry.value]))}
    <div className="flex-1" />
    {item('Settings', <Settings className="h-5 w-5" />, false, onOpenSettings)}
  </nav>;
}
