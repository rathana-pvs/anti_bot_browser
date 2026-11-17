import { useState } from 'react';
import { MapPin } from 'lucide-react';
import { ANY_LOCATION, filterByLocation, proxyLocations, ProxyItem } from '../types/proxy';
import { Select } from './ui/Select';
import { cn } from './ui/cn';

/** A city filter for any proxy list or picker. `keepId` (the current choice) is never filtered out. */
export function useProxyLocationFilter(proxies: ProxyItem[], keepId?: string | null) {
  const [city, setCity] = useState(ANY_LOCATION);
  const { cities } = proxyLocations(proxies);
  // A city that left the list (its proxies were taken) falls back to all cities.
  const current = city === ANY_LOCATION || cities.some(c => c.name === city) ? city : ANY_LOCATION;
  const filtered = filterByLocation(proxies, ANY_LOCATION, current, keepId);
  const active = current !== ANY_LOCATION;
  const reset = () => setCity(ANY_LOCATION);

  const controls = ({ className, selectClassName, icon = false }: { className?: string; selectClassName?: string; icon?: boolean } = {}) =>
    <div className={cn('flex min-w-0 items-center gap-2', className)}>
      {icon && <MapPin className="h-3.5 w-3.5 shrink-0 text-zinc-500" aria-hidden="true" />}
      <div className="min-w-0 flex-1">
        <Select value={current} onValueChange={setCity} ariaLabel="Filter proxies by city" className={selectClassName} disabled={!cities.length}
          options={[{ value: ANY_LOCATION, label: 'All cities' }, ...cities.map(c => ({ value: c.name, label: `${c.name} (${c.count})` }))]} />
      </div>
    </div>;

  return { filtered, active, reset, controls };
}
