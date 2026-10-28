import { KeyboardEvent, PointerEvent, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Circle, MonitorSmartphone, Square, X } from 'lucide-react';
import { AndroidRuntime } from '../../types/device';
import { deviceLiveUrl } from '../../services/devices';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';

type ViewState = { kind: 'idle' } | { kind: 'connecting'; message: string } | { kind: 'live' } | { kind: 'error'; message: string };

const KEY_HOME = 3;
const KEY_BACK = 4;
const KEY_RECENT = 187;
const KEYCODES: Record<string, number> = {
  Enter: 66, Backspace: 67, Delete: 112, Tab: 61, Escape: KEY_BACK,
  ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22,
};
const CONFIG_FLAG = 1n << 63n;
const KEY_FRAME_FLAG = 1n << 62n;
const PTS_MASK = KEY_FRAME_FLAG - 1n;

/** `avc1.PPCCLL` from the SPS in an Annex B config packet. */
function h264Codec(config: Uint8Array): string | null {
  for (let i = 0; i + 4 < config.length; i++) {
    const startCode = config[i] === 0 && config[i + 1] === 0 && (config[i + 2] === 1 || (config[i + 2] === 0 && config[i + 3] === 1));
    if (!startCode) continue;
    const nal = i + (config[i + 2] === 1 ? 3 : 4);
    if ((config[nal] & 0x1f) === 7 && nal + 3 < config.length) {
      return `avc1.${[1, 2, 3].map(offset => config[nal + offset].toString(16).padStart(2, '0')).join('')}`;
    }
  }
  return null;
}

export function PhoneLiveView({ runtime }: { runtime: AndroidRuntime }) {
  const [state, setState] = useState<ViewState>({ kind: 'idle' });
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const decoderRef = useRef<VideoDecoder | null>(null);
  const pointerDown = useRef(false);
  const userName = runtime.android_user_name || `User ${runtime.android_user_id}`;

  const stop = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    if (decoderRef.current && decoderRef.current.state !== 'closed') decoderRef.current.close();
    decoderRef.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  const send = (message: object) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify(message));
  };

  const start = () => {
    stop();
    if (typeof VideoDecoder === 'undefined') {
      setState({ kind: 'error', message: 'This app’s web view can’t decode video (WebCodecs is unavailable), so the phone screen can’t be shown.' });
      return;
    }
    setState({ kind: 'connecting', message: 'Connecting…' });
    let pendingConfig: Uint8Array | null = null;
    let configuredCodec = '';
    let waitingForKeyFrame = true;
    const fail = (message: string) => { setState({ kind: 'error', message }); stop(); };

    const decoder = new VideoDecoder({
      output: (frame) => {
        const canvas = canvasRef.current;
        if (canvas) {
          if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
            canvas.width = frame.displayWidth;
            canvas.height = frame.displayHeight;
            setSize({ width: frame.displayWidth, height: frame.displayHeight });
          }
          canvas.getContext('2d')?.drawImage(frame, 0, 0);
        }
        frame.close();
      },
      error: () => fail('The phone’s video couldn’t be decoded.'),
    });
    decoderRef.current = decoder;

    const socket = new WebSocket(deviceLiveUrl(runtime.device_serial, runtime.android_user_id));
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;
    // Handle messages strictly in order: configuring the decoder is async.
    let queue = Promise.resolve();
    const handle = async (event: MessageEvent) => {
      if (typeof event.data === 'string') {
        const message = JSON.parse(event.data) as { type: string; message?: string; width?: number; height?: number };
        if (message.type === 'status') setState({ kind: 'connecting', message: message.message ?? 'Connecting…' });
        if (message.type === 'error') fail(message.message ?? 'The phone screen stopped.');
        if (message.type === 'meta') setState({ kind: 'live' });
        return;
      }
      const view = new DataView(event.data as ArrayBuffer);
      const flags = view.getBigUint64(0);
      const payload = new Uint8Array(event.data as ArrayBuffer, 12, view.getUint32(8));
      if (flags & CONFIG_FLAG) {
        pendingConfig = payload.slice();
        const codec = h264Codec(pendingConfig);
        if (!codec) return fail('The phone sent a video format this view doesn’t understand.');
        if (codec !== configuredCodec) {
          const support = await VideoDecoder.isConfigSupported({ codec, optimizeForLatency: true });
          if (!support.supported) return fail(`This app’s web view can’t decode the phone’s video (${codec}).`);
          if (decoder.state === 'closed') return;
          decoder.configure({ codec, optimizeForLatency: true });
          configuredCodec = codec;
          waitingForKeyFrame = true;
        }
        return;
      }
      const key = (flags & KEY_FRAME_FLAG) !== 0n;
      if (waitingForKeyFrame && !key) return;
      waitingForKeyFrame = false;
      let data: Uint8Array = payload;
      if (pendingConfig) {
        // Config travels with the next frame, as scrcpy's own client does.
        data = new Uint8Array(pendingConfig.length + payload.length);
        data.set(pendingConfig);
        data.set(payload, pendingConfig.length);
        pendingConfig = null;
      }
      if (decoder.state === 'configured') {
        decoder.decode(new EncodedVideoChunk({ type: key ? 'key' : 'delta', timestamp: Number(flags & PTS_MASK), data }));
      }
    };
    socket.onmessage = (event) => { queue = queue.then(() => handle(event)); };
    socket.onerror = () => fail('Couldn’t connect to the phone screen. Check that the local service is running.');
    socket.onclose = () => setState(current => current.kind === 'live' || current.kind === 'connecting'
      ? { kind: 'error', message: 'The phone screen disconnected.' } : current);
  };

  const touch = (event: PointerEvent<HTMLCanvasElement>, action: 0 | 1 | 2) => {
    const canvas = canvasRef.current;
    if (!canvas || !canvas.width) return;
    const rect = canvas.getBoundingClientRect();
    send({
      type: 'touch', action, width: canvas.width, height: canvas.height,
      x: Math.round((event.clientX - rect.left) / rect.width * canvas.width),
      y: Math.round((event.clientY - rect.top) / rect.height * canvas.height),
    });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLCanvasElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (KEYCODES[event.key] !== undefined) send({ type: 'key', keycode: KEYCODES[event.key] });
    else if (event.key.length === 1) send({ type: 'text', text: event.key });
    else return;
    event.preventDefault();
  };

  if (state.kind === 'idle' || (state.kind === 'error' && !size)) {
    return <div className="flex flex-1 items-center justify-center p-6">
      <section className="w-full max-w-md rounded-panel border border-border bg-surface/70 px-6 py-8 text-center">
        <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl border border-blue-500/20 bg-blue-500/10 text-blue-400">
          <MonitorSmartphone className="h-7 w-7" aria-hidden="true" />
        </div>
        <h2 className="text-base font-semibold text-zinc-100">Phone screen</h2>
        <p className="mt-2 text-xs leading-5 text-zinc-400">
          Shows and controls the phone as {userName}. If another Android user is in front, the phone switches to {userName} first.
        </p>
        {state.kind === 'error' && <Alert variant="warning" className="mt-5 text-left">{state.message}</Alert>}
        <Button className="mt-6" onClick={start}>{state.kind === 'error' ? 'Try again' : 'Show phone screen'}</Button>
      </section>
    </div>;
  }

  return <div className="flex min-h-0 flex-1 flex-col items-center gap-3 p-4">
    <div className="flex w-full max-w-md items-center gap-2 text-xs">
      <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-zinc-400" role="status">
        <span className={state.kind === 'live' ? 'h-1.5 w-1.5 rounded-full bg-emerald-400' : 'h-1.5 w-1.5 rounded-full bg-amber-400'} />
        {state.kind === 'live' ? `Live · ${userName}` : state.message}
      </span>
      {state.kind === 'error' && <Button size="sm" onClick={start}>Reconnect</Button>}
      <IconButton aria-label="Close phone screen" onClick={() => { stop(); setSize(null); setState({ kind: 'idle' }); }}><X className="h-4 w-4" /></IconButton>
    </div>
    <div className="flex min-h-0 flex-1 items-center justify-center">
      <div className="h-full rounded-[2rem] border-4 border-zinc-800 bg-black p-1.5 shadow-xl"
        style={{ aspectRatio: size ? `${size.width} / ${size.height}` : '9 / 20' }}>
        <canvas ref={canvasRef} tabIndex={0} aria-label={`Screen of ${userName}’s phone. Click to tap, type to enter text.`}
          className="h-full w-full touch-none rounded-[1.6rem] bg-zinc-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          onPointerDown={(event) => { if (event.button !== 0) return; pointerDown.current = true; event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.focus(); touch(event, 0); }}
          onPointerMove={(event) => { if (pointerDown.current) touch(event, 2); }}
          onPointerUp={(event) => { if (!pointerDown.current) return; pointerDown.current = false; touch(event, 1); }}
          onPointerCancel={(event) => { if (!pointerDown.current) return; pointerDown.current = false; touch(event, 1); }}
          onKeyDown={onKeyDown}
          onPaste={(event) => { const text = event.clipboardData.getData('text'); if (text) send({ type: 'text', text: text.slice(0, 100) }); event.preventDefault(); }} />
      </div>
    </div>
    <div className="flex items-center gap-6" role="toolbar" aria-label="Phone buttons">
      <IconButton aria-label="Back" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_BACK })}><ArrowLeft className="h-4 w-4" /></IconButton>
      <IconButton aria-label="Home" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_HOME })}><Circle className="h-4 w-4" /></IconButton>
      <IconButton aria-label="Recent apps" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_RECENT })}><Square className="h-3.5 w-3.5" /></IconButton>
    </div>
  </div>;
}
