import { ReactNode } from 'react';
import { cn } from './cn';

export function Field({ label, htmlFor, description, error, children, className }: {
  label: string; htmlFor?: string; description?: string; error?: string; children: ReactNode; className?: string;
}) {
  return <div className={cn('space-y-1.5', className)}>
    <label htmlFor={htmlFor} className="block text-xs font-medium text-zinc-300">{label}</label>
    {children}
    {description && !error && <p className="text-[11px] text-zinc-500">{description}</p>}
    {error && <p role="alert" className="text-[11px] text-red-400">{error}</p>}
  </div>;
}
