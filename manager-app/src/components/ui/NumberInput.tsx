import { useEffect, useId, useState } from 'react';
import { Minus, Plus } from 'lucide-react';

interface NumberInputProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onValueChange: (value: number) => void;
}

/** A themed number field with readable, keyboard-accessible step controls. */
export function NumberInput({ label, value, min, max, step = 1, onValueChange }: NumberInputProps) {
  const id = useId();
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);

  const clamp = (number: number) => Math.max(min, Math.min(max, Math.round(number)));
  const commit = (number: number) => {
    const next = clamp(number);
    setDraft(String(next));
    onValueChange(next);
  };
  const draftValue = draft.trim() === '' ? value : Number(draft);
  const current = Number.isFinite(draftValue) ? draftValue : value;
  const buttonClass = 'flex h-9 w-9 shrink-0 items-center justify-center text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent';

  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1 block text-xs text-zinc-300">{label}</label>
      <div className="flex items-center overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950 shadow-sm transition-colors hover:border-zinc-700 focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/20">
        <button type="button" aria-label={`Decrease ${label}`} disabled={current <= min}
          onClick={() => commit(current - step)} className={`${buttonClass} border-r border-zinc-800`}>
          <Minus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <input id={id} type="number" inputMode="numeric" min={min} max={max} step={step} value={draft}
          className="h-9 w-full min-w-0 bg-transparent px-2 text-center font-mono text-xs text-zinc-100 outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
          onChange={(event) => {
            const raw = event.target.value;
            setDraft(raw);
            const number = Number(raw);
            if (raw.trim() && Number.isInteger(number) && number >= min && number <= max) onValueChange(number);
          }}
          onBlur={() => commit(current)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
              event.preventDefault();
              commit(current + (event.key === 'ArrowUp' ? step : -step));
            } else if (event.key === 'Enter') {
              event.preventDefault();
              commit(current);
            }
          }} />
        <button type="button" aria-label={`Increase ${label}`} disabled={current >= max}
          onClick={() => commit(current + step)} className={`${buttonClass} border-l border-zinc-800`}>
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
