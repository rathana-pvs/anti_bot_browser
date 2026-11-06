import { useState } from 'react';
import { MapPin } from 'lucide-react';
import { ANY_LOCATION, filterByLocation, proxyLocations, ProxyItem } from '../types/proxy';
import { Select } from './ui/Select';
import { cn } from './ui/cn';

/** State and city filters for any proxy list or picker. `keepId` (the current choice) is never filtered out. */
export function useProxyLocationFilter(proxies: ProxyItem[], keepId?: string | null) {
  const [state, setState] = useState(ANY_LOCATION);
  const [city, setCity] = useState(ANY_LOCATION);
  const { states, cities } = proxyLocations(proxies, state);
  const filtered = filterByLocation(proxies, state, city, keepId);
  const active = state !== ANY_LOCATION || city !== ANY_LOCATION;
  const reset = () => { setState(ANY_LOCATION); setCity(ANY_LOCATION); };

  const controls = ({ className, selectClassName, icon = false }: { className?: string; selectClassName?: string; icon?: boolean } = {}) =>
    <div className={cn('flex min-w-0 items-center gap-2', className)}>
      {icon && <MapPin className="h-3.5 w-3.5 shrink-0 text-zinc-500" aria-hidden="true" />}
      <div className="min-w-0 flex-1">
        <Select value={state} ariaLabel="Filter proxies by state" className={selectClassName} disabled={!states.length}
          onValueChange={(value) => { setState(value); setCity(ANY_LOCATION); }}
          options={[{ value: ANY_LOCATION, label: 'All states' }, ...states.map(s => ({ value: s.name, label: `${s.name} (${s.count})` }))]} />
      </div>
      <div className="min-w-0 flex-1">
        <Select value={city} onValueChange={setCity} ariaLabel="Filter proxies by city" className={selectClassName} disabled={!cities.length}
          options={[{ value: ANY_LOCATION, label: 'All cities' }, ...cities.map(c => ({ value: c.name, label: `${c.name} (${c.count})` }))]} />
      </div>
    </div>;

  return { filtered, active, reset, controls };
}
