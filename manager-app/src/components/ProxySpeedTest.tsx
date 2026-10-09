import { useEffect, useRef, useState } from 'react';
import { Gauge, Loader2 } from 'lucide-react';
import { NetworkSpeedResult, testNetworkSpeed } from '../services/api';
import { NetworkIntent } from '../types/profile';

export function ProxySpeedTest({ network, disabled = false }: { network: NetworkIntent; disabled?: boolean }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<NetworkSpeedResult | null>(null);
  const [error, setError] = useState('');
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  const valid = network.mode === 'pool' || (network.mode === 'custom' && !!network.host?.trim()
    && Number.isInteger(network.port) && Number(network.port) >= 1 && Number(network.port) <= 65535);
  if (network.mode === 'direct') return null;

  const test = async () => {
    if (testing) return;
    setTesting(true);
    setResult(null);
    setError('');
    try {
      const measurement = await testNetworkSpeed(network);
      if (active.current) setResult(measurement);
    } catch (err) {
      if (active.current) setError(err instanceof Error ? err.message : 'Speed test failed');
    } finally {
      if (active.current) setTesting(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-zinc-800 pt-2">
      <button type="button" onClick={test} disabled={disabled || testing || !valid}
        className="flex items-center gap-1.5 rounded border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50">
        {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Gauge className="h-3.5 w-3.5" />}
        {testing ? 'Testing speed…' : result ? 'Test again' : 'Test speed'}
      </button>
      <div aria-live="polite" aria-busy={testing} className="space-y-1 text-[11px]">
        {testing && <p className="text-zinc-400">Checking latency, download and upload. This may take up to a minute.</p>}
        {result && <>
          <div className="grid grid-cols-3 gap-2 text-zinc-300">
            <span>Latency<br /><strong>{result.latency.success ? `${result.latency.latency_ms} ms` : 'Failed'}</strong></span>
            <span>Download<br /><strong>{result.download.success ? `${result.download.mbps} Mbps` : 'Failed'}</strong></span>
            <span>Upload<br /><strong>{result.upload.success ? `${result.upload.mbps} Mbps` : 'Failed'}</strong></span>
          </div>
          {[result.latency, result.download, result.upload].map((measurement, index) =>
            !measurement.success && <p key={index} className="text-amber-400">{['Latency', 'Download', 'Upload'][index]}: {measurement.error || 'Test failed'}</p>)}
          <p className="text-zinc-500">Tested {new Date(result.last_checked).toLocaleTimeString()}. Speed is an estimate.</p>
        </>}
        {error && <p role="alert" className="text-red-400">{error}</p>}
      </div>
      <p className="text-[10px] text-zinc-500">Uses Cloudflare: 5 MB download + 2 MB upload. Latency measures the proxy connection.</p>
    </div>
  );
}
