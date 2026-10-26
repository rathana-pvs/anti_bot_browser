import type { QueueExecutionItem } from '../types/automation';

const labels: Record<string, string> = {
  waiting_network: 'Waiting for network',
  confirming_network: 'Confirming network stability',
  waiting_review: 'Network recovered · publication needs review',
  ready: 'Ready to retry · waiting for slot',
  retrying: 'Recovery retry in progress',
  expired: 'Recovery expired · needs review',
  retry_limit: 'Retry limit reached · needs review',
  needs_review: 'Recovery stopped · needs review',
};

export function NetworkRecoveryStatus({ recovery, details = false }: {
  recovery: QueueExecutionItem['network_recovery'];
  details?: boolean;
}) {
  if (!recovery || !labels[recovery.state]) return null;
  const measurement = recovery.last_measurement;
  const speed = (value?: number | null) => value == null ? 'Unavailable' : `${value.toFixed(2)} Mbps`;
  const time = (value: string) => new Date(value).toLocaleString();
  return (
    <div className="text-[11px] text-amber-300" role="status">
      <span>{labels[recovery.state]}</span>
      {recovery.next_check_at && <span className="text-zinc-400"> · Next check {time(recovery.next_check_at)}</span>}
      {details && <div className="mt-1 space-y-1 text-zinc-400">
        <p>Recovery deadline: {time(recovery.deadline_at)} · Automatic retries: {recovery.retry_count}/2</p>
        <p>Required: upload and download above 1 Mbps, two passing checks at least 30 seconds apart, and Facebook reachable.</p>
        {measurement && <p>Upload: {speed(measurement.upload?.mbps)} · Download: {speed(measurement.download?.mbps)} · Facebook: {measurement.facebook?.success ? 'Reachable' : 'Unconfirmed'}</p>}
        {measurement?.error && <p>{measurement.error}</p>}
        {recovery.last_checked_at && <p>Last checked: {time(recovery.last_checked_at)}</p>}
        {recovery.state === 'waiting_review' && <p>Confirm the previous Reel outcome using Review before another upload. Network speed cannot establish publication.</p>}
      </div>}
    </div>
  );
}
