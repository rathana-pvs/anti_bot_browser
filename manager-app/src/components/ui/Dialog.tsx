import * as DialogPrimitive from '@radix-ui/react-dialog';
import { ReactNode } from 'react';
import { X } from 'lucide-react';
import { IconButton } from './IconButton';
import { cn } from './cn';

export function Dialog({ open, onOpenChange, title, description, children, footer, className }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: string;
  children: ReactNode; footer?: ReactNode; className?: string;
}) {
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-[300] bg-black/80 backdrop-blur-sm" />
      <DialogPrimitive.Content className={cn(
        'fixed left-1/2 top-1/2 z-[301] w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-panel border border-zinc-800 bg-canvas shadow-panel outline-none',
        className,
      )}>
        <header className="flex items-start gap-3 border-b border-zinc-800 px-5 py-4">
          <div className="min-w-0 flex-1">
            <DialogPrimitive.Title className="text-sm font-semibold text-foreground">{title}</DialogPrimitive.Title>
            {description && <DialogPrimitive.Description className="mt-1 text-xs text-zinc-400">{description}</DialogPrimitive.Description>}
          </div>
          <DialogPrimitive.Close asChild><IconButton aria-label="Close dialog"><X className="h-4 w-4" /></IconButton></DialogPrimitive.Close>
        </header>
        <div className="p-5">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-zinc-800 px-5 py-4">{footer}</footer>}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  </DialogPrimitive.Root>;
}
