import { HTMLAttributes } from 'react';
import { cn } from './cn';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('rounded-panel border border-zinc-800 bg-surface/70 p-4 shadow-sm', className)} {...props} />;
}
