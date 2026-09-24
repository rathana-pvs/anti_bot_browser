declare module '@novnc/novnc' {
  export default class RFB {
    scaleViewport: boolean;
    resizeSession: boolean;
    constructor(target: HTMLElement, url: string, options?: Record<string, any>);
    disconnect(): void;
    clipboardPasteFrom(text: string): void;
    addEventListener(type: string, listener: (e: any) => void): void;
    removeEventListener(type: string, listener: (e: any) => void): void;
  }
}
