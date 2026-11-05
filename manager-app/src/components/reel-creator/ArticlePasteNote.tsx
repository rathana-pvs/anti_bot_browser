/** Says a paste was reduced to the article, with the way back. */
export function ArticlePasteNote({ restore }: { restore: (() => void) | null }) {
  if (!restore) return null;
  return <p role="status" className="text-xs text-zinc-500">Kept only the article text.{' '}
    <button type="button" onClick={restore} className="text-zinc-300 underline underline-offset-2 hover:text-white">Use everything I pasted</button></p>;
}
