import { AlertCircle, AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import { HTMLAttributes } from 'react';
import { cn } from './cn';

const styles = {
  info: ['border-blue-900 bg-blue-950/50 text-blue-200', Info],
  success: ['border-emerald-900 bg-emerald-950/50 text-emerald-200', CheckCircle2],
  warning: ['border-amber-900 bg-amber-950/50 text-amber-200', AlertTriangle],
  danger: ['border-red-900 bg-red-950/50 text-red-200', AlertCircle],
} as const;

export function Alert({ variant = 'info', className, children, ...props }: HTMLAttributes<HTMLDivElement> & {
  variant?: keyof typeof styles;
}) {
  const [style, Icon] = styles[variant];
  return <div role={variant === 'danger' || variant === 'warning' ? 'alert' : 'status'}
    className={cn('flex items-start gap-2 rounded-control border p-3 text-xs', style, className)} {...props}>
    <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
    <div className="min-w-0 flex-1">{children}</div>
  </div>;
}
