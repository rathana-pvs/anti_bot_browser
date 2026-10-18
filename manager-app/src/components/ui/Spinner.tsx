import { Loader2 } from 'lucide-react';
import { cn } from './cn';

export function Spinner({ label = 'Loading', className }: { label?: string; className?: string }) {
  return <Loader2 role="status" aria-label={label} className={cn('h-4 w-4 animate-spin motion-reduce:animate-none', className)} />;
}
