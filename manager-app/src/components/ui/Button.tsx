import { ButtonHTMLAttributes, forwardRef } from 'react';
import { cn } from './cn';

export type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

const variants: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover',
  secondary: 'bg-elevated text-foreground hover:bg-zinc-700',
  outline: 'border border-border bg-transparent text-zinc-300 hover:bg-elevated hover:text-white',
  ghost: 'bg-transparent text-zinc-400 hover:bg-elevated hover:text-white',
  danger: 'bg-danger text-white hover:bg-red-500',
};

const sizes: Record<ButtonSize, string> = {
  sm: 'min-h-8 px-3 py-1.5 text-xs',
  md: 'min-h-9 px-4 py-2 text-xs',
  lg: 'min-h-11 px-5 py-2.5 text-sm',
  icon: 'h-9 w-9 p-0',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'primary', size = 'md', loading = false, disabled, children, ...props }, ref,
) {
  return <button ref={ref} disabled={disabled || loading} aria-busy={loading || undefined}
    className={cn(
      'inline-flex items-center justify-center gap-2 rounded-control font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 disabled:cursor-not-allowed disabled:opacity-50',
      variants[variant], sizes[size], className,
    )} {...props}>{children}</button>;
});
