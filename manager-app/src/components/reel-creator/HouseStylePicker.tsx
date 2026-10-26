import { BookmarkPlus, Trash2 } from 'lucide-react';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { Select } from '../ui/Select';
import { useAppDialog } from '../ui/AppDialogProvider';
import { matchesHouseStyle } from '../../services/reelDocument';
import type { HouseStyle, Reel } from '../../types/reelCreator';

interface Props {
  styles: HouseStyle[];
  reel: Reel;
  busy: boolean;
  onPick: (style: HouseStyle) => void;
  onSave: (name: string) => void;
  onDelete: (id: string) => void;
}

/** The newsroom's saved looks: apply one, save the current look as one, or delete the one in use. */
export function HouseStylePicker({ styles, reel, busy, onPick, onSave, onDelete }: Props) {
  const { showPrompt, showConfirm } = useAppDialog();
  const current = styles.find(style => matchesHouseStyle(reel, style));
  const save = async () => {
    const name = await showPrompt('Name this house style. It keeps the look, label, accent, ratio, motion, music and styles, not the words or photos.',
      { title: 'Save house style', confirmLabel: 'Save', placeholder: 'e.g. Our breaking style' });
    if (name?.trim()) onSave(name);
  };
  const remove = async (style: HouseStyle) => {
    if (await showConfirm(`Reels already made keep their look.`, { title: `Delete the house style "${style.name}"?`, confirmLabel: 'Delete', variant: 'danger' })) onDelete(style.id);
  };
  return <div className="flex items-center gap-1.5">
    {styles.length > 0 && <div className="w-48"><Select value={current?.id ?? ''} placeholder="House style" ariaLabel="House style" disabled={busy}
      options={styles.map(style => ({ value: style.id, label: style.name }))}
      onValueChange={id => { const style = styles.find(s => s.id === id); if (style) onPick(style); }} /></div>}
    {current
      ? <IconButton aria-label={`Delete house style ${current.name}`} title="Delete this house style" disabled={busy} onClick={() => { void remove(current); }}><Trash2 className="h-3.5 w-3.5" /></IconButton>
      : <Button variant="outline" size="sm" disabled={busy} onClick={() => { void save(); }}><BookmarkPlus className="h-3.5 w-3.5" />Save style</Button>}
  </div>;
}
