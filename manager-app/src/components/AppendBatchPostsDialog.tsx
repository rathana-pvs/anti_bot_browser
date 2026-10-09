import React, { useState } from 'react';
import { Film, Image as ImageIcon, Loader2, Paperclip, Plus, Trash2, X } from 'lucide-react';
import { appendPostsToBatch, getSharedMediaUrl, uploadMediaFiles } from '../services/api';
import { AppendBatchPostInput, DailyBatch } from '../types/automation';

interface DraftRow extends AppendBatchPostInput {
  id: string;
  media_name: string;
}

interface AppendBatchPostsDialogProps {
  batch: DailyBatch;
  onClose: () => void;
  onAppended: () => void | Promise<void>;
}

const emptyRow = (): DraftRow => ({
  id: `append_${Date.now()}_${Math.random().toString(16).slice(2)}`,
  type: 'photo',
  media_file: '',
  media_name: '',
  base_caption: '',
  first_comment: '',
  ai_spin: true,
});

export const AppendBatchPostsDialog: React.FC<AppendBatchPostsDialogProps> = ({
  batch,
  onClose,
  onAppended,
}) => {
  const [rows, setRows] = useState<DraftRow[]>([emptyRow()]);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateRow = (id: string, patch: Partial<DraftRow>) => {
    setRows((current) => current.map((row) => row.id === id ? { ...row, ...patch } : row));
  };

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    setUploading(true);
    setError(null);
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append('files', file));
      const result = await uploadMediaFiles(formData);
      const uploadedRows = result.files.map((file) => ({
        ...emptyRow(),
        type: file.type,
        media_file: file.filename,
        media_name: file.original_name || file.filename,
      }));
      setRows((current) => {
        const withoutBlank = current.filter((row) => row.media_file || row.base_caption.trim());
        return [...withoutBlank, ...uploadedRows];
      });
    } catch (err: any) {
      setError(err.message || 'Failed to upload media');
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  const handleSubmit = async () => {
    const invalid = rows.some((row) => !row.media_file && !row.base_caption.trim());
    if (!rows.length || invalid) {
      setError('Every row needs media or a caption.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await appendPostsToBatch(batch.batch_id, rows.map(({ id: _id, media_name: _name, ...row }) => row));
      await onAppended();
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to append posts');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4" onClick={() => !submitting && onClose()}>
      <div className="w-full max-w-3xl max-h-[88vh] bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden shadow-2xl flex flex-col" onClick={(event) => event.stopPropagation()}>
        <div className="px-4 py-3 border-b border-zinc-800 bg-zinc-900/80 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-white">Add posts to {batch.name}</h3>
            <p className="text-[11px] text-zinc-500 mt-0.5">New posts join the end of this queued or running batch. Account concurrency follows Resource Mode.</p>
          </div>
          <button type="button" onClick={onClose} disabled={submitting} className="p-1.5 rounded-lg text-zinc-500 hover:text-white hover:bg-zinc-800">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 overflow-y-auto space-y-3">
          {error && <div className="px-3 py-2 rounded-lg bg-red-950/60 border border-red-800 text-xs text-red-200">{error}</div>}
          <div className="flex flex-wrap items-center gap-2">
            <label className="px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-xs font-semibold text-white flex items-center gap-1.5 cursor-pointer">
              {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Paperclip className="w-3.5 h-3.5" />}
              Upload media
              <input type="file" accept="image/*,video/*" multiple className="hidden" onChange={handleUpload} disabled={uploading || submitting} />
            </label>
            <button type="button" onClick={() => setRows((current) => [...current, emptyRow()])} className="px-3 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-200 flex items-center gap-1.5">
              <Plus className="w-3.5 h-3.5" /> Add text post
            </button>
            <span className="text-[11px] text-zinc-500">{rows.length} post{rows.length === 1 ? '' : 's'} to append</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {rows.map((row, index) => (
              <div key={row.id} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3 space-y-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-mono text-zinc-500">#{index + 1}</span>
                  {row.media_file ? (
                    row.type === 'reel'
                      ? <Film className="w-3.5 h-3.5 text-purple-400" />
                      : <ImageIcon className="w-3.5 h-3.5 text-blue-400" />
                  ) : null}
                  <span className="text-xs text-zinc-300 truncate flex-1">{row.media_name || 'Text post'}</span>
                  {rows.length > 1 && (
                    <button type="button" onClick={() => setRows((current) => current.filter((item) => item.id !== row.id))} className="p-1 text-zinc-600 hover:text-red-400">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
                {row.media_file && row.type === 'photo' && (
                  <img src={getSharedMediaUrl(row.media_file)} alt="" className="w-full h-24 object-cover rounded-lg bg-black" />
                )}
                <textarea
                  rows={2}
                  value={row.base_caption}
                  onChange={(event) => updateRow(row.id, { base_caption: event.target.value })}
                  placeholder="Caption (optional when media is attached)"
                  className="w-full px-2.5 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500 resize-y"
                />
                <input
                  value={row.first_comment || ''}
                  onChange={(event) => updateRow(row.id, { first_comment: event.target.value })}
                  placeholder="First comment (optional)"
                  className="w-full px-2.5 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500"
                />
              </div>
            ))}
          </div>
        </div>

        <div className="px-4 py-3 border-t border-zinc-800 bg-zinc-900/70 flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} disabled={submitting} className="px-3 py-2 rounded-lg bg-zinc-800 text-xs text-zinc-300 hover:bg-zinc-700">Cancel</button>
          <button type="button" onClick={handleSubmit} disabled={submitting || uploading} className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-xs font-semibold text-white flex items-center gap-1.5">
            {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Append {rows.length} post{rows.length === 1 ? '' : 's'}
          </button>
        </div>
      </div>
    </div>
  );
};
