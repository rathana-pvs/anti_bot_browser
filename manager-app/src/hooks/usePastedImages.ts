import { useEffect, useRef, useState } from 'react';
import { isDesktopApp } from '../services/desktopEnvironment';
import { readHostClipboardImage, readHostClipboardText } from '../services/clipboard';

/** The images on a paste, named for upload; a copied browser image often has no useful file name. */
export function pastedImages(data: Pick<DataTransfer, 'files'> | null): File[] {
  return Array.from(data?.files ?? []).filter(file => file.type.startsWith('image/')).map((file, i) => {
    if (file.name && file.name !== 'image.png') return file;
    const extension = file.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
    return new File([file], `pasted-${Date.now()}-${i + 1}.${extension}`, { type: file.type });
  });
}

/** Why a Ctrl+V outside a text field pasted no photo, from the text the clipboard holds instead. */
export function noPhotoMessage(text: string): string {
  return /^https?:\/\/\S+$/i.test(text.trim())
    ? 'That is a link to the photo, not the photo. Right-click the photo and choose Copy image, then press Ctrl+V.'
    : 'There is no photo to paste. Right-click a photo and choose Copy image, then press Ctrl+V.';
}

function editing(target: EventTarget | null) {
  return target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA'].includes(target.tagName));
}

function message(err: unknown) {
  return `The copied photo could not be read${err instanceof Error && err.message ? `: ${err.message}` : '.'}`;
}

/**
 * Ctrl+V anywhere on the page hands copied images to `onImages`, with no file saved first. A paste with no image into
 * a text field is left alone; outside one, `onProblem` says why nothing pasted. `pasting` stays true while the photo
 * is read and until the promise `onImages` returns settles, so the upload shows as loading too.
 */
export function usePastedImages(onImages: (files: File[]) => Promise<unknown> | void, enabled: boolean, onProblem?: (message: string) => void) {
  const handler = useRef(onImages);
  handler.current = onImages;
  const problem = useRef(onProblem);
  problem.current = onProblem;
  const [pasting, setPasting] = useState(false);
  const busy = useRef(false);
  useEffect(() => {
    if (!enabled) return;
    const deliver = async (read: () => Promise<File[]>, quietWhenEmpty: boolean) => {
      if (busy.current) return;
      busy.current = true;
      setPasting(true);
      try {
        const files = await read();
        if (files.length) await handler.current(files);
        else if (!quietWhenEmpty) problem.current?.(noPhotoMessage(await readHostClipboardText().catch(() => '')));
      } catch (err) {
        problem.current?.(message(err));
      } finally {
        busy.current = false;
        setPasting(false);
      }
    };
    if (isDesktopApp()) {
      // The desktop webview leaves images out of paste events, and fires none outside a text field: read Ctrl+V natively.
      const key = (event: KeyboardEvent) => {
        if (event.key.toLowerCase() !== 'v' || !(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey || event.repeat) return;
        void deliver(async () => { const file = await readHostClipboardImage(); return file ? [file] : []; }, editing(event.target));
      };
      window.addEventListener('keydown', key);
      return () => window.removeEventListener('keydown', key);
    }
    const paste = (event: ClipboardEvent) => {
      const files = pastedImages(event.clipboardData);
      const quiet = editing(event.target);
      if (!files.length && quiet) return;
      event.preventDefault();
      const text = event.clipboardData?.getData('text/plain') ?? '';
      if (!files.length) { problem.current?.(noPhotoMessage(text)); return; }
      void deliver(async () => files, true);
    };
    window.addEventListener('paste', paste);
    return () => window.removeEventListener('paste', paste);
  }, [enabled]);
  return pasting;
}
