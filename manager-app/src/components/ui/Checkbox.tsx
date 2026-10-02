import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import { Check } from 'lucide-react';
import { twMerge } from 'tailwind-merge';

interface CheckboxProps {
  id?: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
}

export function Checkbox({ id, checked, onCheckedChange, ariaLabel, disabled, className }: CheckboxProps) {
  return (
    <CheckboxPrimitive.Root
      checked={checked}
      id={id}
      onCheckedChange={(value) => onCheckedChange(value === true)}
      aria-label={ariaLabel}
      disabled={disabled}
      className={twMerge(
        'flex h-4 w-4 shrink-0 items-center justify-center rounded border border-zinc-600 bg-zinc-950 text-white outline-none transition-colors hover:border-zinc-500 focus:ring-2 focus:ring-blue-500/40 data-[state=checked]:border-blue-500 data-[state=checked]:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
    >
      <CheckboxPrimitive.Indicator>
        <Check className="h-3 w-3" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
