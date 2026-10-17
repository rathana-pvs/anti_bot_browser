import { cn } from './cn';

export interface TabOption<T extends string> { value: T; label: string; disabled?: boolean }

export function Tabs<T extends string>({ value, options, onValueChange, ariaLabel, tone = 'blue' }: {
  value: T; options: TabOption<T>[]; onValueChange: (value: T) => void; ariaLabel: string; tone?: 'blue' | 'rose';
}) {
  return <div role="tablist" aria-label={ariaLabel} className="flex flex-wrap gap-2">
    {options.map(option => <button key={option.value} role="tab" type="button" disabled={option.disabled}
      aria-selected={value === option.value} onClick={() => onValueChange(option.value)}
      className={cn('rounded-control px-3 py-2 text-xs transition-colors disabled:opacity-40',
        value === option.value
          ? tone === 'rose' ? 'bg-rose-950 text-rose-200' : 'bg-blue-950 text-blue-200'
          : 'text-zinc-400 hover:bg-elevated hover:text-white')}>{option.label}</button>)}
  </div>;
}
