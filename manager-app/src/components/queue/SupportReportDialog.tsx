import { useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { QueueExecutionItem } from '../../types/automation';
import { downloadSupportBundle } from '../../services/api';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Dialog } from '../ui/Dialog';
import { Textarea } from '../ui/Textarea';

export function SupportReportDialog({ open, item, profileName, onClose, onSaved, onError }: {
  open: boolean; item: QueueExecutionItem | null; profileName?: string;
  onClose: () => void; onSaved: (location: string) => void; onError: (message: string) => void;
}) {
  const [description, setDescription] = useState('');
  const [includeEvidence, setIncludeEvidence] = useState(false);
  const [includeContent, setIncludeContent] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    setDescription(''); setIncludeEvidence(false); setIncludeContent(false);
  }, [open, item?.execution_id]);

  const download = async () => {
    setBusy(true);
    try {
      const location = await downloadSupportBundle({
        executionId: item?.execution_id,
        description: description.trim(),
        includeEvidence: Boolean(item) && includeEvidence,
        includeContent: Boolean(item) && includeContent,
      });
      onSaved(location);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Couldn’t create the support file.');
    } finally {
      setBusy(false);
    }
  };

  return <Dialog open={open} onOpenChange={(next) => { if (!next && !busy) onClose(); }} className="max-w-lg"
    title={item ? 'Report a problem with this item' : 'Export diagnostics'}
    description="Creates a local ZIP you can send to support."
    footer={<>
      <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
      <Button onClick={download} loading={busy}><Download className="h-3.5 w-3.5" />Save support ZIP</Button>
    </>}>
    <div className="space-y-4 text-xs">
      {item && <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
        <dt className="text-zinc-500">Account</dt><dd className="text-zinc-200">{profileName || item.profile_id}</dd>
        <dt className="text-zinc-500">Status</dt><dd className="text-zinc-200">{item.status}{item.stage ? ` · ${item.stage}` : ''}</dd>
        <dt className="text-zinc-500">ID</dt><dd className="break-all font-mono text-[10px] text-zinc-400">{item.execution_id}</dd>
      </dl>}
      <label className="block">
        <span className="mb-1.5 block font-medium text-zinc-300">What happened? <span className="font-normal text-zinc-600">(optional)</span></span>
        <Textarea value={description} onChange={(e) => setDescription(e.target.value.slice(0, 4000))} rows={4}
          placeholder="The Reel published, but the first comment didn’t appear." />
      </label>
      {item && <div className="space-y-2">
        <label htmlFor="report-include-evidence" className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-zinc-800 p-3 hover:border-zinc-700">
          <Checkbox id="report-include-evidence" checked={includeEvidence} onCheckedChange={setIncludeEvidence} className="mt-0.5" />
          <span><span className="block font-medium text-zinc-200">Include evidence screenshots</span>
            <span className="mt-0.5 block text-[11px] text-amber-400/90">Screenshots may show page content or account names.</span></span>
        </label>
        <label htmlFor="report-include-content" className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-zinc-800 p-3 hover:border-zinc-700">
          <Checkbox id="report-include-content" checked={includeContent} onCheckedChange={setIncludeContent} className="mt-0.5" />
          <span><span className="block font-medium text-zinc-200">Include caption, comment, media name, and post link</span>
            <span className="mt-0.5 block text-[11px] text-zinc-500">Useful when the problem is about content matching.</span></span>
        </label>
      </div>}
      <p className="rounded-xl border border-emerald-900/60 bg-emerald-950/20 p-3 text-[11px] leading-relaxed text-emerald-200/80">
        Browser data, cookies, passwords, proxy credentials, access tokens, and email addresses are never included. Logs are sanitized automatically.
      </p>
    </div>
  </Dialog>;
}
