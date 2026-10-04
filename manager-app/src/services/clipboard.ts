import { readText as readNativeClipboardText } from '@tauri-apps/plugin-clipboard-manager';

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
