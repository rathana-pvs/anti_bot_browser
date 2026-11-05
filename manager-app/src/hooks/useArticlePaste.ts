import { ClipboardEvent, useState } from 'react';
import { cleanArticleText } from '../services/articleText';

const sameWords = (a: string, b: string) => a.replace(/\s+/g, ' ').trim() === b.replace(/\s+/g, ' ').trim();

/** Pasting into an article box keeps only the article; `restore` puts back what was pasted until the text is edited. */
export function useArticlePaste(value: string, setValue: (value: string) => void) {
  const [last, setLast] = useState<{ cleaned: string; original: string } | null>(null);
  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = event.clipboardData.getData('text/plain');
    const clean = cleanArticleText(pasted);
    if (sameWords(pasted, clean)) return;
    event.preventDefault();
    const { selectionStart, selectionEnd, value: current } = event.currentTarget;
    const around = (middle: string) => current.slice(0, selectionStart) + middle + current.slice(selectionEnd);
    setLast({ cleaned: around(clean), original: around(pasted) });
    setValue(around(clean));
  };
  const restore = last && last.cleaned === value ? () => { setValue(last.original); setLast(null); } : null;
  return { onPaste, restore };
}
