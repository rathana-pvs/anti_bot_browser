import React, { ReactNode } from 'react';
import { ChevronDown, ChevronRight, Folder, KeyRound, Layers, Plus, Settings2 } from 'lucide-react';
import { PROXY_FOLDERS, ProxyItem, ProxySource } from '../types/proxy';
import { ALL_FOLDERS } from './accounts/FolderList';
import { cn } from './ui/cn';

export const ALL_SOURCES = '__all_sources__';

/** The Proxies sidebar: All proxies, then each source. The open one lists its automatic folders. */
export const ProxySourceList: React.FC<{
  sources: ProxySource[];
  proxies: ProxyItem[];
  sourceId: string;
  folder: string;
  onSelect: (sourceId: string, folder: string) => void;
  onAdd: () => void;
  onEdit: (source: ProxySource) => void;
}> = ({ sources, proxies, sourceId, folder, onSelect, onAdd, onEdit }) => {
  const inSource = (id: string) => (proxy: ProxyItem) => id === ALL_SOURCES || (proxy.source_id || 'default') === id;

  const row = (id: string, label: string, icon: ReactNode, count: number, extra?: ReactNode, title?: string) => {
    const open = sourceId === id;
    const current = open && folder === ALL_FOLDERS;
    const Chevron = open ? ChevronDown : ChevronRight;
    return <div key={id}>
      <div className={cn('group flex items-center rounded-lg text-xs transition-colors',
        current ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200')}>
        <button type="button" onClick={() => onSelect(id, ALL_FOLDERS)} aria-current={current ? 'true' : undefined} aria-expanded={open} title={title}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
          <Chevron className="h-3 w-3 shrink-0 opacity-60" aria-hidden="true" />
          {icon}<span className="min-w-0 flex-1 truncate">{label}</span>
          <span className="text-[11px] text-zinc-500">{count}</span>
        </button>
        {extra}
      </div>
      {open && <div className="space-y-0.5 py-0.5">
        {PROXY_FOLDERS.map(name => {
          const active = folder === name;
          const n = proxies.filter(p => inSource(id)(p) && p.group === name).length;
          return <button key={name} type="button" onClick={() => onSelect(id, name)} aria-current={active ? 'true' : undefined}
            className={cn('flex w-full items-center gap-2 rounded-lg py-1.5 pl-8 pr-2.5 text-left text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500',
              active ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200')}>
            <Folder className="h-3.5 w-3.5" aria-hidden="true" /><span className="min-w-0 flex-1 truncate">{name}</span>
            <span className="text-[11px] text-zinc-500">{n}</span>
          </button>;
        })}
      </div>}
    </div>;
  };

  return <aside aria-label="Proxy sources" className="flex w-60 shrink-0 flex-col border-r border-border bg-surface/30">
    <div className="flex items-center justify-between px-4 pb-2 pt-3">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Sources</span>
      <button type="button" onClick={onAdd} aria-label="Add proxy source" title="Add proxy source"
        className="rounded p-1 text-zinc-400 outline-none hover:bg-zinc-800 hover:text-zinc-100 focus-visible:ring-2 focus-visible:ring-blue-500">
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
    <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
      {row(ALL_SOURCES, 'All proxies', <Layers className="h-3.5 w-3.5" aria-hidden="true" />, proxies.length)}
      {sources.map(source => row(source.id, source.name,
        <KeyRound aria-hidden="true" className={cn('h-3.5 w-3.5', source.provider === 'webshare' ? (source.key_set ? 'text-emerald-400' : 'text-amber-400') : 'text-zinc-500')} />,
        proxies.filter(inSource(source.id)).length,
        <button type="button" onClick={() => onEdit(source)} aria-label={`Settings for ${source.name}`} title="Source settings"
          className="mr-1 rounded p-1 text-zinc-500 opacity-0 outline-none hover:bg-zinc-700 hover:text-zinc-100 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-blue-500 group-hover:opacity-100">
          <Settings2 className="h-3 w-3" />
        </button>,
        `${source.provider_label} · ${source.protocol.toUpperCase()}${source.provider === 'webshare' ? (source.key_set ? ' · API key saved' : ' · no API key yet') : ''}`))}
      <p className="px-2.5 pt-3 text-[11px] leading-relaxed text-zinc-500">Folders update automatically from assignments and speed checks.</p>
    </nav>
  </aside>;
};
