import * as Dialog from '@radix-ui/react-dialog';
import { useCallback, useEffect, useRef, useState } from 'react';
import { detectNetworkTimezone } from '../services/api';
import { NetworkIntent, RequestedEnvironment } from '../types/profile';
import { Select } from './ui/Select';

/** Confirm a fallback before saving it as an explicit manual timezone. */
export function useTimezoneConfirmation() {
  const [options, setOptions] = useState<string[] | null>(null);
  const [timezone, setTimezone] = useState('');
  const [source, setSource] = useState<'host' | 'proxy'>('host');
  const pending = useRef<((value: string | null) => void) | null>(null);

  useEffect(() => () => {
    pending.current?.(null);
    pending.current = null;
  }, []);

  const settle = useCallback((value: string | null) => {
    const resolve = pending.current;
    pending.current = null;
    setOptions(null);
    resolve?.(value);
  }, []);

  const confirmTimezone = async (environment: RequestedEnvironment, network: NetworkIntent): Promise<RequestedEnvironment | null> => {
    if (network.mode === 'direct' && environment.timezone_policy === 'manual') return environment;
    const detection = await detectNetworkTimezone(network);
    if (detection.detected) {
      return { ...environment, timezone_policy: detection.source, timezone: detection.timezone };
    }
    setSource(detection.source);
    const fallback = detection.timezone;
    const chosen = await new Promise<string | null>((resolve) => {
      pending.current = resolve;
      setTimezone(fallback);
      setOptions(detection.timezone_options);
    });
    return chosen ? { ...environment, timezone_policy: 'manual', timezone: chosen } : null;
  };

  const timezoneDialog = (
    <Dialog.Root open={options !== null} onOpenChange={(open) => { if (!open) settle(null); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[300] bg-black/80 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[301] w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border border-zinc-800 bg-zinc-950 p-5 text-zinc-100 shadow-2xl">
          <Dialog.Title className="text-sm font-semibold">Confirm timezone</Dialog.Title>
          <Dialog.Description className="mt-2 text-xs leading-relaxed text-zinc-400">
            {source === 'proxy' ? 'The timezone of your proxy’s exit IP could not be detected.' : 'Your computer’s timezone could not be detected.'} Choose and confirm the timezone to use for this profile.
          </Dialog.Description>
          <label className="mt-4 block text-xs text-zinc-400">
            Timezone
            <Select
              value={timezone}
              onValueChange={setTimezone}
              ariaLabel="Timezone"
              className="mt-1.5 border-zinc-700 bg-zinc-900 text-sm"
              contentClassName="z-[302]"
              options={(options || []).map((value) => ({
                value,
                label: value === 'Asia/Phnom_Penh' ? 'Cambodia — Asia/Phnom_Penh'
                  : value === 'America/Guatemala' ? 'Guatemala — America/Guatemala'
                    : `US — ${value}`,
              }))}
            />
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" onClick={() => settle(null)} className="rounded border border-zinc-700 px-3 py-2 text-xs">Cancel</button>
            <button type="button" disabled={!timezone} onClick={() => settle(timezone)} className="rounded bg-blue-600 px-3 py-2 text-xs font-semibold disabled:opacity-50">Confirm timezone</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );

  return { confirmTimezone, timezoneDialog, awaitingTimezoneConfirmation: options !== null };
}
