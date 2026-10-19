import { Loader2, RefreshCw } from 'lucide-react';

export function BackendConnectionScreen({ error, onRetry, onOpenSetup }: {
  error: string | null; onRetry: () => void; onOpenSetup?: () => void;
}) {
  return <main className="flex h-screen items-center justify-center bg-[#08090c] p-6 text-zinc-300">
    <div className="max-w-md space-y-4 text-center">
      {!error && <Loader2 className="mx-auto h-7 w-7 animate-spin text-blue-400" />}
      <h1 className="text-base font-medium text-zinc-100">{error ? 'Waiting for the backend' : 'Starting the backend…'}</h1>
      <p role={error ? 'alert' : 'status'} className="text-sm leading-6 text-zinc-400">
        {error || 'Connecting to local services. Your profiles will appear when they are ready.'}
      </p>
      {error && <div className="flex justify-center gap-3">
        <button type="button" onClick={onRetry} className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500"><RefreshCw className="h-4 w-4" />Retry connection</button>
        {onOpenSetup && <button type="button" onClick={onOpenSetup} className="rounded-lg border border-zinc-700 px-4 py-2 text-sm">Setup and repair</button>}
      </div>}
    </div>
  </main>;
}
