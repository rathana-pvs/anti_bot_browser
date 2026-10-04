import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';

export type ToastVariant = 'success' | 'error' | 'warning' | 'info';

interface ToastProps {
  variant?: ToastVariant;
  message: string;
  onDismiss: () => void;
}

export function Toast({ message, onDismiss, variant = 'info' }: ToastProps) {
  const [fading, setFading] = useState(false);
  const dismissRef = useRef(onDismiss);

  useEffect(() => {
    dismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    setFading(false);
    const fadeTimer = window.setTimeout(() => setFading(true), 3500);
    const dismissTimer = window.setTimeout(() => dismissRef.current(), 3800);
    return () => {
      window.clearTimeout(fadeTimer);
      window.clearTimeout(dismissTimer);
    };
  }, [message]);

  const colors = {
    success: 'border-emerald-800 bg-emerald-950 text-emerald-300',
    error: 'border-red-800 bg-red-950 text-red-200',
    warning: 'border-amber-700 bg-amber-950 text-amber-200',
    info: 'border-blue-800 bg-blue-950 text-blue-200',
  };
  const Icon = variant === 'success' ? CheckCircle2 : variant === 'info' ? Info : AlertTriangle;

  return (
    <div
      role={variant === 'error' || variant === 'warning' ? 'alert' : 'status'}
      className={`pointer-events-auto flex w-full items-center gap-3 rounded-xl border p-3 text-xs ${colors[variant]} shadow-lg transition-opacity duration-300 motion-reduce:transition-none ${fading ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{message}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="shrink-0 rounded p-1 text-current hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-current"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
