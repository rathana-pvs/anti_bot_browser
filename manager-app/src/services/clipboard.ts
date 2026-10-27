import { readImage as readNativeClipboardImage, readText as readNativeClipboardText, writeText as writeNativeClipboardText } from '@tauri-apps/plugin-clipboard-manager';

export async function writeHostClipboardText(text: string): Promise<void> {
  try {
    await writeNativeClipboardText(text);
  } catch (nativeError) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    throw nativeError;
  }
}

export async function readHostClipboardText(): Promise<string> {
  try {
    return await readNativeClipboardText();
  } catch (nativeError) {
    if (navigator.clipboard?.readText) {
      return navigator.clipboard.readText();
    }
    throw nativeError;
  }
}

/**
 * The image on the desktop clipboard as a PNG file, or null when it holds none. The desktop webview does not hand
 * copied images to the page's paste event, so the app reads them natively.
 */
export async function readHostClipboardImage(): Promise<File | null> {
  let image;
  try { image = await readNativeClipboardImage(); } catch { return null; }  // text, or nothing, on the clipboard
  const [{ width, height }, rgba] = await Promise.all([image.size(), image.rgba()]);
  if (!width || !height) return null;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  canvas.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(rgba), width, height), 0, 0);
  const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'));
  return blob ? new File([blob], `pasted-${Date.now()}.png`, { type: 'image/png' }) : null;
}
