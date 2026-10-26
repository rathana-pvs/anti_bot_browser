import { ReactNode, useEffect, useRef, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { cn } from './cn';

export interface ActionMenuItem {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  hint?: string;
}

/** Small overflow menu for secondary actions that should not crowd a toolbar. */
export function ActionMenu({ items, ariaLabel, label, className }: {
  items: ActionMenuItem[]; ariaLabel: string; label?: ReactNode; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  return <div ref={root} className={cn('relative', className)}>
    <button type="button" aria-label={ariaLabel} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}
      className="inline-flex min-h-8 items-center gap-1.5 rounded-control border border-border px-2.5 text-xs text-zinc-300 transition-colors hover:bg-elevated hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">
      {label}<MoreHorizontal className="h-4 w-4" />
    </button>
    {open && <div role="menu" className="absolute right-0 top-full z-50 mt-1 min-w-52 rounded-xl border border-zinc-700 bg-zinc-950 p-1 shadow-xl">
      {items.map(item => <button key={item.label} type="button" role="menuitem" disabled={item.disabled} title={item.hint}
        onClick={() => { setOpen(false); item.onSelect(); }}
        className={cn('flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40',
          item.danger ? 'text-red-300 hover:bg-red-950/60' : 'text-zinc-200 hover:bg-zinc-800')}>
        {item.icon}<span>{item.label}</span>
      </button>)}
    </div>}
  </div>;
}
