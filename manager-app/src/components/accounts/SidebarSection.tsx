import { ReactNode, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

const storageKey = (id: string) => `accounts.sidebar.folded.${id}`;
const readFolded = (id: string) => { try { return localStorage.getItem(storageKey(id)) === '1'; } catch { return false; } };

/**
 * A foldable part of the Accounts sidebar, remembered per section. Folded, it still shows the
 * open item (`activeChild`), so the folder you are in never disappears.
 */
export function SidebarSection({ id, title, icon, hint, children, activeChild, className = 'pt-3' }: {
  id: string; title: string; icon?: ReactNode; hint?: string;
  children: ReactNode; activeChild?: ReactNode; className?: string;
}) {
  const [folded, setFolded] = useState(() => readFolded(id));
  const toggle = () => {
    const next = !folded;
    setFolded(next);
    try { localStorage.setItem(storageKey(id), next ? '1' : '0'); } catch { /* remembered for this visit only */ }
  };
  const Chevron = folded ? ChevronRight : ChevronDown;
  return <div role="group" aria-label={title} className={className}>
    <button type="button" onClick={toggle} aria-expanded={!folded} title={hint}
      className="group flex w-full items-center gap-1.5 rounded-md px-2.5 pb-1 pt-0.5 text-left text-[10px] font-semibold uppercase tracking-wider text-zinc-500 outline-none hover:text-zinc-300 focus-visible:ring-2 focus-visible:ring-blue-500">
      {icon}<span className="min-w-0 flex-1 truncate">{title}</span>
      <Chevron className="h-3 w-3 shrink-0 opacity-60 group-hover:opacity-100" aria-hidden="true" />
    </button>
    <div className="space-y-0.5">{folded ? activeChild : children}</div>
  </div>;
}
