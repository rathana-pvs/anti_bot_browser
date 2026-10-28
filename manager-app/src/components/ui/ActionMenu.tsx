import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';
import { cn } from './cn';

export interface ActionMenuItem {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  hint?: string;
  /** Draws a separator line above this item. */
  divider?: boolean;
}

/** Small overflow menu for secondary actions that should not crowd a toolbar. */
export function ActionMenu({ items, ariaLabel, label, className }: {
  items: ActionMenuItem[]; ariaLabel: string; label?: ReactNode; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const root = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  // Escape clipping ancestors and keep the menu within the viewport.
  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return;
    const anchor = trigger.current.getBoundingClientRect();
    const { width, height } = menu.current.getBoundingClientRect();
    const below = anchor.bottom + 4;
    const top = below + height <= window.innerHeight - 8 ? below : anchor.top - height - 4;
    setPosition({
      left: Math.max(8, Math.min(anchor.right - width, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(top, window.innerHeight - height - 8)),
    });
  }, [open, items]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape'
        : !root.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnViewportChange = () => setOpen(false);
    const closeOnScroll = (event: Event) => {
      if (!menu.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    document.addEventListener('scroll', closeOnScroll, true);
    window.addEventListener('resize', closeOnViewportChange);
    window.addEventListener('blur', closeOnViewportChange);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
      document.removeEventListener('scroll', closeOnScroll, true);
      window.removeEventListener('resize', closeOnViewportChange);
      window.removeEventListener('blur', closeOnViewportChange);
    };
  }, [open]);
  return <div ref={root} className={cn('relative', className)}>
    <button ref={trigger} type="button" aria-label={ariaLabel} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}
      className="inline-flex min-h-8 items-center gap-1.5 rounded-control border border-border px-2.5 text-xs text-zinc-300 transition-colors hover:bg-elevated hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">
      {label}<MoreHorizontal className="h-4 w-4" />
    </button>
    {open && createPortal(<div ref={menu} role="menu" aria-label={ariaLabel}
      style={{ left: position.left, top: position.top, maxHeight: 'calc(100vh - 16px)', maxWidth: 'calc(100vw - 16px)' }}
      className="fixed z-[400] min-w-[min(13rem,calc(100vw-16px))] overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-950 p-1 shadow-xl">
      {items.map((item, index) => <div key={`${item.label}-${index}`}>{item.divider && index > 0 && <div className="my-1 border-t border-zinc-800" role="separator" />}<button type="button" role="menuitem" disabled={item.disabled} title={item.hint}
        onClick={() => { setOpen(false); item.onSelect(); }}
        className={cn('flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40',
          item.danger ? 'text-red-300 hover:bg-red-950/60' : 'text-zinc-200 hover:bg-zinc-800')}>
        {item.icon}<span>{item.label}</span>
      </button></div>)}
    </div>, document.body)}
  </div>;
}
