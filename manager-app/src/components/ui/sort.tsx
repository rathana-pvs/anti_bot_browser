import { useState } from 'react';
import { ArrowUp } from 'lucide-react';
import { cn } from './cn';

export type SortDir = 'asc' | 'desc';
export interface SortState<K extends string> { key: K; dir: SortDir }

/** A click cycles a column ascending → descending → off (back to the list's default order). */
export function useSort<K extends string>(initial: SortState<K> | null = null) {
  const [sort, setSort] = useState<SortState<K> | null>(initial);
  const toggle = (key: K) => setSort((current) =>
    current && current.key === key
      ? current.dir === 'asc' ? { key, dir: 'desc' } : null
      : { key, dir: 'asc' });
  return { sort, toggle };
}

/** Numbers compare numerically; strings compare naturally (so "Account 2" sorts before "Account 10"). Nulls sort last. */
export function compareValues(a: string | number | null | undefined, b: string | number | null | undefined): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

/** Sorts a copy of `rows` by the active column, or returns the input untouched when no column is active. */
export function sortRows<T, K extends string>(rows: T[], sort: SortState<K> | null, value: (row: T, key: K) => string | number | null | undefined): T[] {
  if (!sort) return rows;
  const direction = sort.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => direction * compareValues(value(a, sort.key), value(b, sort.key)));
}

/** A clickable column header. Shows a direction arrow when active, and a faint one on hover otherwise. */
export function SortButton<K extends string>({ label, column, sort, onToggle, className }: {
  label: React.ReactNode; column: K; sort: SortState<K> | null; onToggle: (key: K) => void; className?: string;
}) {
  const active = sort?.key === column;
  return (
    <button type="button" onClick={() => onToggle(column)}
      className={cn('group inline-flex items-center gap-1 text-left transition-colors hover:text-zinc-300', active && 'text-zinc-200', className)}>
      {label}
      <ArrowUp aria-hidden="true" className={cn('h-3 w-3 shrink-0 transition',
        active ? 'text-zinc-300' : 'text-zinc-500 opacity-0 group-hover:opacity-100',
        active && sort?.dir === 'desc' && 'rotate-180')} />
    </button>
  );
}
