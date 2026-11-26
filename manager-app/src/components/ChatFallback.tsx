import { useState } from 'react';
import { readHostClipboardText, writeHostClipboardText } from '../services/clipboard';

const button = 'rounded-lg border border-zinc-700 px-3 py-2 text-xs text-blue-300 disabled:opacity-40';
const cls = 'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-xs text-zinc-200';

/** When the AI API is overloaded or out of quota: copy the same prompt into any AI chat, then paste its reply back. */
export function ChatFallback({ open, onToggle, busy, disabled = false, what, getPrompt, onReply }: {
  open: boolean; onToggle: (open: boolean) => void; busy: boolean; disabled?: boolean;
  /** What the chat writes, such as "captions" or "the summary". */
  what: string;
  getPrompt: () => Promise<string>;
  /** Uses the pasted reply. Throws to show why it could not. */
  onReply: (reply: string) => Promise<void>;
}) {
  const [reply, setReply] = useState('');
  const [note, setNote] = useState('');
  // Shown only when the clipboard refused the prompt, to copy by hand.
  const [shown, setShown] = useState('');
  const act = async (action: () => Promise<void>) => {
    setNote('');
    try { await action(); } catch (error) { setNote(error instanceof Error ? error.message : 'That did not work.'); }
  };
  const copy = () => act(async () => {
    const prompt = await getPrompt();
    setShown('');
    try { await writeHostClipboardText(prompt); } catch {
      setShown(prompt);
      setNote('Could not copy automatically. Select all the text in the box above, copy it, and paste it into any AI chat.');
      return;
    }
    setNote('Prompt copied. Paste it into Gemini, ChatGPT or any AI chat, then paste the whole reply below.');
  });
  const paste = () => act(async () => setReply(await readHostClipboardText()));
  return <details open={open} onToggle={event => onToggle(event.currentTarget.open)} className="space-y-2 rounded-lg border border-zinc-800 p-2">
    <summary className="cursor-pointer text-zinc-400">AI busy or out of quota? Write {what} in any AI chat</summary>
    <div className="space-y-2 pt-2">
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} onClick={copy} disabled={busy || disabled}>1. Copy prompt</button>
        <button type="button" className={button} onClick={paste} disabled={busy}>2. Paste reply from clipboard</button>
      </div>
      {shown && <textarea readOnly className={cls} rows={4} aria-label="Prompt to copy" value={shown} onFocus={event => event.currentTarget.select()} />}
      <textarea className={cls} rows={4} aria-label="AI chat reply" placeholder="…or paste the AI chat's reply here"
        value={reply} onChange={event => setReply(event.target.value)} />
      <button type="button" className={button} disabled={busy || !reply.trim()}
        onClick={() => act(async () => { await onReply(reply); setReply(''); })}>3. Use reply</button>
      {note && <p role="status" className="text-zinc-300">{note}</p>}
    </div>
  </details>;
}
