import { KeyboardEvent, PointerEvent, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Circle, MonitorSmartphone, Square, X } from 'lucide-react';
import { AndroidRuntime } from '../../types/device';
import { deviceLiveUrl } from '../../services/devices';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { Spinner } from '../ui/Spinner';
import { h264DecoderConfig, h264Frame } from './phoneVideo';

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
    let lastConfig: VideoDecoderConfig | null = null;
    let lastKeyFrame: { data: Uint8Array; timestamp: number } | null = null;
    let waitingForKeyFrame = true;
    let receivedFrame = false;
    // The web view may pick a hardware decoder that refuses the stream; the software ones decode it.
    let acceleration: HardwareAcceleration = 'no-preference';
    const fail = (message: string) => { setState({ kind: 'error', message }); stop(); };

    const draw = (frame: VideoFrame) => {
      if (socketRef.current !== socket) { frame.close(); return; }
      const canvas = canvasRef.current;
      if (canvas) {
        if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
          canvas.width = frame.displayWidth;
          canvas.height = frame.displayHeight;
          setSize({ width: frame.displayWidth, height: frame.displayHeight });
        }
        canvas.getContext('2d')?.drawImage(frame, 0, 0);
        if (!receivedFrame) {
          receivedFrame = true;
          setState({ kind: 'live' });
        }
      }
      frame.close();
    };
    const configure = (config: VideoDecoderConfig) => decoder.configure({ ...config, hardwareAcceleration: acceleration });
    const decodeFailed = (error: DOMException) => {
      if (socketRef.current !== socket) return;
      if (acceleration !== 'prefer-software' && lastConfig) {
        // A decoder that failed is closed: start a software one and resume at the next key frame.
        acceleration = 'prefer-software';
        decoder = new VideoDecoder({ output: draw, error: decodeFailed });
        decoderRef.current = decoder;
        configure(lastConfig);
        waitingForKeyFrame = true;
        // A still screen sends no frames: show the last picture now and ask the phone for a new key frame.
        if (lastKeyFrame) decoder.decode(new EncodedVideoChunk({ type: 'key', ...lastKeyFrame }));
        send({ type: 'reset_video' });
        return;
      }
      fail(`The phone’s video couldn’t be decoded${error?.message ? ` (${error.message})` : ''}.`);
    };

    let decoder = new VideoDecoder({ output: draw, error: decodeFailed });
    decoderRef.current = decoder;

    const socket = new WebSocket(deviceLiveUrl(runtime.device_serial, runtime.android_user_id));
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;
    // Handle messages strictly in order: configuring the decoder is async.
    let queue = Promise.resolve();
    const handle = async (event: MessageEvent) => {
      if (socketRef.current !== socket) return;
      if (typeof event.data === 'string') {
        const message = JSON.parse(event.data) as { type: string; message?: string; width?: number; height?: number };
        if (message.type === 'status') setState({ kind: 'connecting', message: message.message ?? 'Connecting…' });
        if (message.type === 'error') fail(message.message ?? 'The phone screen stopped.');
        if (message.type === 'meta') setState({ kind: 'connecting', message: 'Waiting for the phone’s video…' });
        return;
      }
      const view = new DataView(event.data as ArrayBuffer);
      const flags = view.getBigUint64(0);
      const payload = new Uint8Array(event.data as ArrayBuffer, 12, view.getUint32(8));
      if (flags & CONFIG_FLAG) {
        const config = h264DecoderConfig(payload);
        const support = await VideoDecoder.isConfigSupported({ ...config, hardwareAcceleration: acceleration });
        if (socketRef.current !== socket || decoder.state === 'closed') return;
        if (!support.supported) return fail(`This app’s web view can’t decode the phone’s video (${config.codec}).`);
        // A reset/rotation may change SPS/PPS while the codec string remains the same.
        lastConfig = config;
        lastKeyFrame = null;
        configure(config);
        waitingForKeyFrame = true;
        return;
      }
      const key = (flags & KEY_FRAME_FLAG) !== 0n;
      if (!lastConfig || (waitingForKeyFrame && !key)) return;
      waitingForKeyFrame = false;
      const data = h264Frame(payload);
      if (key) lastKeyFrame = { data: data.slice(), timestamp: Number(flags & PTS_MASK) };
      if (decoder.state === 'configured') {
        decoder.decode(new EncodedVideoChunk({ type: key ? 'key' : 'delta', timestamp: Number(flags & PTS_MASK), data }));
      }
    };
    socket.onmessage = (event) => {
      queue = queue.then(() => handle(event)).catch(error => {
        if (socketRef.current === socket) fail(`The phone’s video couldn’t be decoded (${error instanceof Error ? error.message : String(error)}).`);
      });
    };
    socket.onerror = () => { if (socketRef.current === socket) fail('Couldn’t connect to the phone screen. Check that the local service is running.'); };
    socket.onclose = () => {
      if (socketRef.current === socket) setState(current => current.kind === 'live' || current.kind === 'connecting'
        ? { kind: 'error', message: 'The phone screen disconnected.' } : current);
    };
  };

  const touch = (event: PointerEvent<HTMLCanvasElement>, action: 0 | 1 | 2) => {
    if (state.kind !== 'live') return;
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
    if (state.kind !== 'live') return;
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
        {state.kind === 'connecting' ? <Spinner className="shrink-0 text-blue-400" label="Connecting to phone" />
          : <span className={state.kind === 'live' ? 'h-1.5 w-1.5 rounded-full bg-emerald-400' : 'h-1.5 w-1.5 rounded-full bg-amber-400'} />}
        {state.kind === 'live' ? `Live · ${userName}` : state.message}
      </span>
      {state.kind === 'error' && <Button size="sm" onClick={start}>Reconnect</Button>}
      <IconButton aria-label="Close phone screen" onClick={() => { stop(); setSize(null); setState({ kind: 'idle' }); }}><X className="h-4 w-4" /></IconButton>
    </div>
    <div className="flex min-h-0 flex-1 items-center justify-center">
      <div className="relative h-full rounded-[2rem] border-4 border-zinc-800 bg-black p-1.5 shadow-xl"
        style={{ aspectRatio: size ? `${size.width} / ${size.height}` : '9 / 20' }}>
        {state.kind === 'connecting' && <div aria-hidden="true" className="absolute inset-1.5 z-10 flex flex-col items-center justify-center gap-4 rounded-[1.6rem] bg-zinc-950/95 p-6 text-center">
          <Spinner className="h-9 w-9 text-blue-400" />
          <p className="text-sm font-medium text-zinc-200">{state.message}</p>
          <p className="text-xs text-zinc-500">Opening {userName}’s phone screen</p>
        </div>}
        <canvas ref={canvasRef} tabIndex={state.kind === 'live' ? 0 : -1} aria-busy={state.kind === 'connecting'} aria-label={`Screen of ${userName}’s phone. Click to tap, type to enter text.`}
          className="h-full w-full touch-none rounded-[1.6rem] bg-zinc-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          onPointerDown={(event) => { if (event.button !== 0) return; pointerDown.current = true; event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.focus(); touch(event, 0); }}
          onPointerMove={(event) => { if (pointerDown.current) touch(event, 2); }}
          onPointerUp={(event) => { if (!pointerDown.current) return; pointerDown.current = false; touch(event, 1); }}
          onPointerCancel={(event) => { if (!pointerDown.current) return; pointerDown.current = false; touch(event, 1); }}
          onKeyDown={onKeyDown}
          onPaste={(event) => { const text = event.clipboardData.getData('text'); if (state.kind === 'live' && text) send({ type: 'text', text: text.slice(0, 100) }); event.preventDefault(); }} />
      </div>
    </div>
    <div className="flex items-center gap-6" role="toolbar" aria-label="Phone buttons">
      <IconButton aria-label="Back" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_BACK })}><ArrowLeft className="h-4 w-4" /></IconButton>
      <IconButton aria-label="Home" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_HOME })}><Circle className="h-4 w-4" /></IconButton>
      <IconButton aria-label="Recent apps" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_RECENT })}><Square className="h-3.5 w-3.5" /></IconButton>
    </div>
  </div>;
}
