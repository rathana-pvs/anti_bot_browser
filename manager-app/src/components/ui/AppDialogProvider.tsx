import * as Dialog from '@radix-ui/react-dialog';
import { AlertTriangle, Info, X } from 'lucide-react';
import React, { createContext, useCallback, useContext, useRef, useState } from 'react';

type DialogKind = 'alert' | 'confirm' | 'prompt';
type DialogVariant = 'default' | 'danger';

interface DialogOptions {
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: DialogVariant;
  defaultValue?: string;
  placeholder?: string;
}

interface DialogRequest {
  kind: DialogKind;
  message: string;
  options: DialogOptions;
  resolve: (value: boolean | string | null) => void;
}

interface AppDialogContextValue {
  showAlert: (message: string, options?: DialogOptions) => Promise<void>;
  showConfirm: (message: string, options?: DialogOptions) => Promise<boolean>;
  showPrompt: (message: string, options?: DialogOptions) => Promise<string | null>;
}

const AppDialogContext = createContext<AppDialogContextValue | null>(null);

export function AppDialogProvider({ children }: { children: React.ReactNode }) {
  const [request, setRequest] = useState<DialogRequest | null>(null);
  const [promptValue, setPromptValue] = useState('');
  const queue = useRef<DialogRequest[]>([]);

  const present = useCallback((next: DialogRequest) => {
    setRequest((current) => {
      if (current) {
        queue.current.push(next);
        return current;
      }
      setPromptValue(next.options.defaultValue || '');
      return next;
    });
  }, []);

  const settle = useCallback((value: boolean | string | null) => {
    setRequest((current) => {
      current?.resolve(value);
      const next = queue.current.shift() || null;
      setPromptValue(next?.options.defaultValue || '');
      return next;
    });
  }, []);

  const showAlert = useCallback((message: string, options: DialogOptions = {}) => (
    new Promise<void>((resolve) => present({
      kind: 'alert',
      message,
      options,
      resolve: () => resolve(),
    }))
  ), [present]);

  const showConfirm = useCallback((message: string, options: DialogOptions = {}) => (
    new Promise<boolean>((resolve) => present({
      kind: 'confirm', message, options, resolve: (value) => resolve(value === true),
    }))
  ), [present]);

  const showPrompt = useCallback((message: string, options: DialogOptions = {}) => (
    new Promise<string | null>((resolve) => present({
      kind: 'prompt', message, options,
      resolve: (value) => resolve(typeof value === 'string' ? value : null),
    }))
  ), [present]);

  const variant = request?.options.variant || 'default';
  const isDanger = variant === 'danger';

  return (
    <AppDialogContext.Provider value={{ showAlert, showConfirm, showPrompt }}>
      {children}
      <Dialog.Root
        open={Boolean(request)}
        onOpenChange={(open) => {
          if (!open && request) settle(request.kind === 'alert' ? true : null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[300] bg-black/80 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out" />
          <Dialog.Content
            aria-describedby="app-dialog-description"
            className="fixed left-1/2 top-1/2 z-[301] w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950 text-zinc-100 shadow-2xl shadow-black/70 outline-none"
          >
            <div className="flex items-start gap-3 border-b border-zinc-800 bg-zinc-900/50 px-5 py-4">
              <div className={`rounded-full p-2 ${isDanger ? 'bg-red-950 text-red-400' : 'bg-blue-950 text-blue-400'}`}>
                {isDanger ? <AlertTriangle className="h-5 w-5" /> : <Info className="h-5 w-5" />}
              </div>
              <div className="min-w-0 flex-1">
                <Dialog.Title className={`text-sm font-semibold ${isDanger ? 'text-red-200' : 'text-zinc-100'}`}>
                  {request?.options.title || (request?.kind === 'confirm' ? 'Confirm action' : request?.kind === 'prompt' ? 'Enter information' : 'Notice')}
                </Dialog.Title>
                <Dialog.Description id="app-dialog-description" className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-zinc-400">
                  {request?.message}
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <button className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200" aria-label="Close dialog">
                  <X className="h-4 w-4" />
                </button>
              </Dialog.Close>
            </div>

            {request?.kind === 'prompt' && (
              <div className="px-5 pt-4">
                <input
                  autoFocus
                  value={promptValue}
                  placeholder={request.options.placeholder}
                  onChange={(event) => setPromptValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') settle(promptValue.trim());
                  }}
                  className="w-full rounded-lg border border-zinc-800 bg-black/40 px-3 py-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
                />
              </div>
            )}

            <div className="flex justify-end gap-2 px-5 py-4">
              {request?.kind !== 'alert' && (
                <button
                  onClick={() => settle(null)}
                  className="rounded-md border border-zinc-700 bg-zinc-900 px-4 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-white"
                >
                  {request?.options.cancelLabel || 'Cancel'}
                </button>
              )}
              <button
                autoFocus={request?.kind !== 'prompt'}
                onClick={() => settle(request?.kind === 'prompt' ? promptValue.trim() : true)}
                className={`rounded-md px-4 py-2 text-xs font-semibold transition-colors ${
                  isDanger
                    ? 'bg-red-600 text-white hover:bg-red-500'
                    : 'bg-blue-600 text-white hover:bg-blue-500'
                }`}
              >
                {request?.options.confirmLabel || (request?.kind === 'alert' ? 'Close' : 'Continue')}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </AppDialogContext.Provider>
  );
}

export function useAppDialog() {
  const context = useContext(AppDialogContext);
  if (!context) throw new Error('useAppDialog must be used inside AppDialogProvider');
  return context;
}
