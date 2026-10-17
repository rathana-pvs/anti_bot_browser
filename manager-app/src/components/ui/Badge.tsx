import { HTMLAttributes } from 'react';
import { cn } from './cn';

const variants = {
  neutral: 'border-zinc-700 bg-zinc-800 text-zinc-300',
  success: 'border-emerald-800 bg-emerald-950 text-emerald-300',
  warning: 'border-amber-800 bg-amber-950 text-amber-300',
  danger: 'border-red-800 bg-red-950 text-red-300',
  info: 'border-blue-800 bg-blue-950 text-blue-300',
};

export function Badge({ className, variant = 'neutral', ...props }: HTMLAttributes<HTMLSpanElement> & {
  variant?: keyof typeof variants;
}) {
  return <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold', variants[variant], className)} {...props} />;
}
