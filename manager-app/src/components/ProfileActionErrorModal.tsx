import { useState } from 'react';
import { AlertTriangle, Check, Clipboard, X } from 'lucide-react';

interface ProfileActionErrorModalProps {
  profileName: string;
  profileId: string;
  action: 'start' | 'stop' | 'pause' | 'unpause';
  reason: string;
  onClose: () => void;
}

const actionLabels = {
  start: 'start',
  stop: 'stop',
  pause: 'pause',
  unpause: 'resume',
} as const;

export const ProfileActionErrorModal: React.FC<ProfileActionErrorModalProps> = ({
  profileName,
  profileId,
  action,
  reason,
  onClose,
}) => {
  const [copied, setCopied] = useState(false);
  const diagnostic = reason || 'The backend did not provide an error reason.';

  const copyReason = async () => {
    try {
      await navigator.clipboard.writeText(diagnostic);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = diagnostic;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand('copy');
      textarea.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
  <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="profile-action-error-title">
    <div className="w-full max-w-lg overflow-hidden rounded-xl border border-red-900/70 bg-zinc-950 shadow-2xl">
      <div className="flex items-start justify-between gap-3 border-b border-red-950 bg-red-950/30 px-5 py-4">
        <div className="flex min-w-0 gap-3">
          <div className="rounded-full bg-red-950 p-2 text-red-400">
            <AlertTriangle className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h2 id="profile-action-error-title" className="text-sm font-semibold text-red-200">
              Could not {actionLabels[action]} profile
            </h2>
            <p className="mt-1 truncate text-xs text-zinc-400" title={`${profileName} (${profileId})`}>
              {profileName} · {profileId}
            </p>
          </div>
        </div>
        <button onClick={onClose} className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200" aria-label="Close error">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="space-y-3 px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-medium text-zinc-300">Reason</p>
          <button onClick={copyReason} className="flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800 hover:text-white">
            {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Clipboard className="h-3 w-3" />}
            {copied ? 'Copied' : 'Copy reason'}
          </button>
        </div>
        <pre className="max-h-64 select-text overflow-auto whitespace-pre-wrap break-words rounded-lg border border-zinc-800 bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-red-300">
          {diagnostic}
        </pre>
        {action === 'start' && (
          <p className="text-[11px] leading-relaxed text-zinc-500">
            The profile remains stopped. Correct the reported network, proxy, resource, or container issue before trying again.
          </p>
        )}
      </div>

      <div className="flex justify-end border-t border-zinc-800 px-5 py-3">
        <button autoFocus onClick={onClose} className="rounded-md bg-zinc-100 px-4 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-white">
          Close
        </button>
      </div>
    </div>
  </div>
  );
};
