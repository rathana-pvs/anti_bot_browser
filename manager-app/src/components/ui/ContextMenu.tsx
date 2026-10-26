import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ActionMenuItem } from './ActionMenu';
import { cn } from './cn';

interface MenuState { x: number; y: number; title?: string; items: ActionMenuItem[] }

/**
 * Right-click menu. `open` accepts a mouse event (placed at the pointer) or a keyboard
 * event from Shift+F10 / the Menu key (placed under the focused element).
 */
export function useContextMenu() {
  const [menu, setMenu] = useState<MenuState | null>(null);
  const close = useCallback(() => setMenu(null), []);

  const open = useCallback((event: React.MouseEvent | React.KeyboardEvent, items: ActionMenuItem[], title?: string) => {
    event.preventDefault();
    event.stopPropagation();
    if (!items.length) return;
    if ('clientX' in event && event.clientX) {
      setMenu({ x: event.clientX, y: event.clientY, items, title });
    } else {
      const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
      setMenu({ x: rect.left + 24, y: rect.top + Math.min(rect.height, 32), items, title });
    }
  }, []);

  /** Spread on any element whose keyboard users should be able to open its menu. */
  const keyboardTrigger = useCallback((items: () => ActionMenuItem[], title?: string) => (event: React.KeyboardEvent) => {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) open(event, items(), title);
  }, [open]);

  const element = menu ? <ContextMenuSurface state={menu} onClose={close} /> : null;
  return { open, close, keyboardTrigger, element };
}

function ContextMenuSurface({ state, onClose }: { state: MenuState; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: state.x, top: state.y });

  // Keep the whole menu on screen.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    setPosition({
      left: Math.max(8, Math.min(state.x, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(state.y, window.innerHeight - height - 8)),
    });
    el.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  }, [state]);

  useEffect(() => {
    const closeOnOutside = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) onClose(); };
    const closeOnKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('keydown', closeOnKey);
    window.addEventListener('blur', onClose);
    window.addEventListener('resize', onClose);
    document.addEventListener('scroll', onClose, true);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('keydown', closeOnKey);
      window.removeEventListener('blur', onClose);
      window.removeEventListener('resize', onClose);
      document.removeEventListener('scroll', onClose, true);
    };
  }, [onClose]);

  const moveFocus = (event: React.KeyboardEvent) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])];
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  };

  return createPortal(<div ref={ref} role="menu" aria-label={state.title || 'Actions'} onKeyDown={moveFocus}
    onContextMenu={(event) => event.preventDefault()}
    style={{ left: position.left, top: position.top }}
    className="fixed z-[400] min-w-52 max-w-72 rounded-xl border border-zinc-700 bg-zinc-950 p-1 shadow-xl">
    {state.title && <div className="truncate px-3 pb-1.5 pt-1 text-[11px] text-zinc-500">{state.title}</div>}
    {state.items.map((item, index) => <div key={`${item.label}-${index}`}>
      {item.divider && index > 0 && <div className="my-1 border-t border-zinc-800" role="separator" />}
      <button type="button" role="menuitem" disabled={item.disabled} title={item.hint}
        onClick={() => { onClose(); item.onSelect(); }}
        className={cn('flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs outline-none transition-colors disabled:cursor-not-allowed disabled:opacity-40',
          item.danger ? 'text-red-300 hover:bg-red-950/60 focus:bg-red-950/60' : 'text-zinc-200 hover:bg-zinc-800 focus:bg-zinc-800')}>
        {item.icon}<span className="truncate">{item.label}</span>
      </button>
    </div>)}
  </div>, document.body);
}
