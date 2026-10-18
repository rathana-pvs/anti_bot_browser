import { forwardRef, TextareaHTMLAttributes } from 'react';
import { cn } from './cn';

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props }, ref,
) {
  return <textarea ref={ref} className={cn(
    'w-full rounded-control border border-zinc-700 bg-canvas px-3 py-2 text-xs text-foreground shadow-sm outline-none placeholder:text-zinc-600 hover:border-zinc-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:cursor-not-allowed disabled:opacity-50',
    className,
  )} {...props} />;
});
