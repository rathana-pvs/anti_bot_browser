import * as RadioGroupPrimitive from '@radix-ui/react-radio-group';
import { twMerge } from 'tailwind-merge';

export interface RadioOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
}

interface RadioGroupProps {
  value: string;
  options: RadioOption[];
  onValueChange: (value: string) => void;
  ariaLabel?: string;
  className?: string;
}

export function RadioGroup({ value, options, onValueChange, ariaLabel, className }: RadioGroupProps) {
  return (
    <RadioGroupPrimitive.Root
      value={value}
      onValueChange={onValueChange}
      aria-label={ariaLabel}
      className={twMerge('space-y-2', className)}
    >
      {options.map((option) => (
        <label
          key={option.value}
          className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-zinc-800 bg-zinc-950/70 p-3 text-xs hover:border-zinc-700 has-[[data-state=checked]]:border-blue-600/70 has-[[data-state=checked]]:bg-blue-950/20"
        >
          <RadioGroupPrimitive.Item
            value={option.value}
            disabled={option.disabled}
            className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-zinc-600 bg-zinc-950 outline-none focus:ring-2 focus:ring-blue-500/40 data-[state=checked]:border-blue-500 disabled:opacity-50"
          >
            <RadioGroupPrimitive.Indicator className="h-2 w-2 rounded-full bg-blue-500" />
          </RadioGroupPrimitive.Item>
          <span>
            <span className="block font-medium text-zinc-200">{option.label}</span>
            {option.description && <span className="mt-0.5 block text-[11px] text-zinc-500">{option.description}</span>}
          </span>
        </label>
      ))}
    </RadioGroupPrimitive.Root>
  );
}
