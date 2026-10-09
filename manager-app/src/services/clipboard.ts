import { readText as readNativeClipboardText, writeText as writeNativeClipboardText } from '@tauri-apps/plugin-clipboard-manager';

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
