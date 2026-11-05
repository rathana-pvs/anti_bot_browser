import { useState } from 'react';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Dialog } from '../ui/Dialog';
import { Textarea } from '../ui/Textarea';
import { useArticlePaste } from '../../hooks/useArticlePaste';
import { sourceKind, UPDATE_LABEL } from '../../services/reelDocument';
import { ArticlePasteNote } from './ArticlePasteNote';

interface Props {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onUpdate: (source: string, mark: boolean) => void;
}

/** New reporting on a developing story: rewrite the reel's words from it as a new reel, keeping photos and styles. */
export function UpdateStoryDialog({ open, busy, onOpenChange, onUpdate }: Props) {
  const [source, setSource] = useState('');
  const articlePaste = useArticlePaste(source, setSource);
  const [mark, setMark] = useState(true);
  return <Dialog open={open} onOpenChange={onOpenChange} title="Update this story"
    description="Paste the updated article or its link. The scenes are rewritten from it as a new reel; photos, credits and styles stay, and this reel is kept as it is."
    footer={<>
      <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
      <Button loading={busy} disabled={busy || sourceKind(source) === 'empty'} onClick={() => onUpdate(source, mark)}>Write update</Button>
    </>}>
    <div className="space-y-3">
      <Textarea value={source} onChange={event => setSource(event.target.value)} onPaste={articlePaste.onPaste} rows={6} disabled={busy}
        aria-label="Updated article link or text" placeholder="Paste the updated news link or article text" className="resize-none text-sm leading-relaxed" />
      <ArticlePasteNote restore={articlePaste.restore} />
      <label className="flex items-center gap-2 text-xs text-zinc-300">
        <Checkbox checked={mark} onCheckedChange={setMark} />Label it {UPDATE_LABEL} and use the new date</label>
    </div>
  </Dialog>;
}
