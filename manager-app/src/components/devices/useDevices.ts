import { useCallback, useEffect, useState } from 'react';
import { AndroidDevice } from '../../types/device';
import { fetchDevices } from '../../services/devices';

/** Phones known to this computer, polled so account pages can show where each phone account lives. */
export function useDevices(intervalMs = 20000) {
  const [devices, setDevices] = useState<AndroidDevice[]>([]);
  const reload = useCallback(() => fetchDevices().then(result => setDevices(result.devices)).catch(() => undefined), []);
  useEffect(() => {
    void reload();
    const timer = setInterval(() => void reload(), intervalMs);
    return () => clearInterval(timer);
  }, [reload, intervalMs]);
  return { devices, reload };
}
