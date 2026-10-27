import { useEffect, useRef } from 'react';
import { isDesktopApp } from '../services/desktopEnvironment';
import { readHostClipboardImage } from '../services/clipboard';

/** The images on a paste, named for upload; a copied browser image often has no useful file name. */
export function pastedImages(data: Pick<DataTransfer, 'files'> | null): File[] {
  return Array.from(data?.files ?? []).filter(file => file.type.startsWith('image/')).map((file, i) => {
    if (file.name && file.name !== 'image.png') return file;
    const extension = file.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
    return new File([file], `pasted-${Date.now()}-${i + 1}.${extension}`, { type: file.type });
  });
}

/**
 * Ctrl+V anywhere on the page hands copied images to `onImages`, with no file saved first. A paste with no image
 * (text into a field) is left alone. Some browsers copy only a link to an image; that pastes nothing.
 */
export function usePastedImages(onImages: (files: File[]) => void, enabled: boolean) {
  const handler = useRef(onImages);
  handler.current = onImages;
  useEffect(() => {
    if (!enabled) return;
    if (isDesktopApp()) {
      // The desktop webview leaves images out of paste events, and fires none outside a text field: read Ctrl+V natively.
      const key = (event: KeyboardEvent) => {
        if (event.key.toLowerCase() !== 'v' || !(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey || event.repeat) return;
        void readHostClipboardImage().then(file => { if (file) handler.current([file]); }).catch(() => undefined);
      };
      window.addEventListener('keydown', key);
      return () => window.removeEventListener('keydown', key);
    }
    const paste = (event: ClipboardEvent) => {
      const files = pastedImages(event.clipboardData);
      if (!files.length) return;
      event.preventDefault();
      handler.current(files);
    };
    window.addEventListener('paste', paste);
    return () => window.removeEventListener('paste', paste);
  }, [enabled]);
}
