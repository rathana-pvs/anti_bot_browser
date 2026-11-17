import { useState } from 'react';
import { Layers } from 'lucide-react';
import { ProxyItem, proxySourceCounts } from '../types/proxy';
import { Select } from './ui/Select';
import { cn } from './ui/cn';

const ANY_SOURCE = '__any_source__';

/** A proxy-source filter for pickers, beside the state and city filters. With one source it shows,
 * disabled, so you can still see where the proxies come from.
 * `keepId` (the current choice) is never filtered out; `onSourceChange` gets the new list so the
 * picker can move its choice into the chosen source. */
export function useProxySourceFilter(proxies: ProxyItem[], keepId?: string | null,
  onSourceChange?: (filtered: ProxyItem[]) => void) {
  const [chosen, setChosen] = useState(ANY_SOURCE);
  const sources = proxySourceCounts(proxies);
  const source = sources.some(s => s.id === chosen) ? chosen : ANY_SOURCE;
  const pick = (id: string, list: ProxyItem[]) =>
    id === ANY_SOURCE ? list : list.filter(p => (p.source_id || 'default') === id);
  const filtered = source === ANY_SOURCE ? proxies : proxies.filter(p => (p.source_id || 'default') === source || p.id === keepId);

  const change = (id: string) => {
    setChosen(id);
    onSourceChange?.(pick(id, proxies));
  };

  const controls = ({ className, selectClassName, icon = false }: { className?: string; selectClassName?: string; icon?: boolean } = {}) =>
    <div className={cn('flex min-w-0 items-center gap-2', className)}>
      {icon && <Layers className="h-3.5 w-3.5 shrink-0 text-zinc-500" aria-hidden="true" />}
      <div className="min-w-0 flex-1">
        <Select value={sources.length === 1 ? sources[0].id : source} onValueChange={change} ariaLabel="Proxy source"
          className={selectClassName} disabled={sources.length < 2}
          options={sources.length === 1 ? [{ value: sources[0].id, label: `${sources[0].name} (${sources[0].count} free)` }]
            : [{ value: ANY_SOURCE, label: sources.length ? 'All sources' : 'No proxies' },
              ...sources.map(s => ({ value: s.id, label: `${s.name} (${s.count} free)` }))]} />
      </div>
    </div>;

  return { filtered, source: source === ANY_SOURCE ? null : source, controls };
}
