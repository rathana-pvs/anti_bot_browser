import { KeyboardEvent, PointerEvent, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Circle, MonitorSmartphone, Square, X } from 'lucide-react';
import { AndroidRuntime } from '../../types/device';
import { deviceLiveUrl } from '../../services/devices';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { Spinner } from '../ui/Spinner';
import { h264DecoderConfig, h264Frame } from './phoneVideo';

type ViewState = { kind: 'idle' } | { kind: 'connecting'; message: string } | { kind: 'live' } | { kind: 'asleep' } | { kind: 'error'; message: string };

const KEY_HOME = 3;
const KEY_BACK = 4;
const KEY_RECENT = 187;
const KEY_WAKEUP = 224;
const KEYCODES: Record<string, number> = {
  Enter: 66, Backspace: 67, Delete: 112, Tab: 61, Escape: KEY_BACK,
  ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22,
};
const CONFIG_FLAG = 1n << 63n;
const KEY_FRAME_FLAG = 1n << 62n;
const PTS_MASK = KEY_FRAME_FLAG - 1n;
/** Shape of the empty frame until the phone's first video frame gives its real one. */
const DEFAULT_SCREEN_RATIO = 9 / 20;

export function PhoneLiveView({ runtime, autoStart = false, screenOnly = false, onClose }: {
  runtime: AndroidRuntime; autoStart?: boolean; screenOnly?: boolean; onClose?: () => void;
}) {
  const [state, setState] = useState<ViewState>({ kind: 'idle' });
  const [screenRatio, setScreenRatio] = useState(DEFAULT_SCREEN_RATIO);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const decoderRef = useRef<VideoDecoder | null>(null);
  const pointerDown = useRef(false);
  const videoWaitRef = useRef<number | null>(null);
  const userName = runtime.android_user_name || `User ${runtime.android_user_id}`;

  const stop = useCallback(() => {
    if (videoWaitRef.current !== null) window.clearTimeout(videoWaitRef.current);
    videoWaitRef.current = null;
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
    let screenAwake = true;
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
          setScreenRatio(frame.displayWidth / frame.displayHeight);
        }
        const context = canvas.getContext('2d');
        if (!context) { frame.close(); fail('The phone screen could not be drawn. Close this viewer and try again.'); return; }
        context.drawImage(frame, 0, 0);
        if (!receivedFrame) {
          receivedFrame = true;
          if (videoWaitRef.current !== null) window.clearTimeout(videoWaitRef.current);
          videoWaitRef.current = null;
          setState({ kind: screenAwake ? 'live' : 'asleep' });
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
        if (decoder.state !== 'closed') decoder.close();
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
    videoWaitRef.current = window.setTimeout(() => {
      if (socketRef.current === socket && !receivedFrame) fail('The phone did not finish connecting. Check the phone, then reconnect.');
    }, 180000);
    // Handle messages strictly in order: configuring the decoder is async.
    let queue = Promise.resolve();
    const handle = async (event: MessageEvent) => {
      if (socketRef.current !== socket) return;
      if (typeof event.data === 'string') {
        const message = JSON.parse(event.data) as { type: string; message?: string; width?: number; height?: number; awake?: boolean };
        if (message.type === 'status') setState({ kind: 'connecting', message: message.message ?? 'Connecting…' });
        if (message.type === 'error') fail(message.message ?? 'The phone screen stopped.');
        if (message.type === 'screen') {
          screenAwake = message.awake !== false;
          if (screenAwake) {
            // The last picture is from the sleeping screen, and a still screen sends no new frames.
            send({ type: 'reset_video' });
            setState(receivedFrame ? { kind: 'live' } : { kind: 'connecting', message: 'Waiting for the phone’s video…' });
          } else setState({ kind: 'asleep' });
        }
        if (message.type === 'meta') {
          setState({ kind: 'connecting', message: 'Waiting for the phone’s video…' });
          if (videoWaitRef.current !== null) window.clearTimeout(videoWaitRef.current);
          videoWaitRef.current = window.setTimeout(() => {
            if (socketRef.current !== socket || receivedFrame) return;
            if (lastConfig) decodeFailed(new DOMException('No video frame received'));
            if (socketRef.current !== socket) return;
            send({ type: 'reset_video' });
            videoWaitRef.current = window.setTimeout(() => {
              if (socketRef.current === socket && !receivedFrame) fail('The phone connected but did not show a video frame. Unlock the phone, then reconnect.');
            }, 10000);
          }, 8000);
        }
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
      if (socketRef.current === socket) setState(current => current.kind === 'live' || current.kind === 'connecting' || current.kind === 'asleep'
        ?{ kind: 'error', message: 'The phone screen disconnected.' } : current);
    };
  };

  const startRef = useRef(start);
  startRef.current = start;
  useEffect(() => {
    if (!autoStart) return;
    // StrictMode replays mount effects. Cancel the first scheduled start before
    // it opens a socket and leaves a user switch running on the backend.
    const timer = window.setTimeout(() => startRef.current(), 0);
    return () => window.clearTimeout(timer);
  }, [autoStart]);

  const touch = (event: PointerEvent<HTMLCanvasElement>, action: 0 | 1 | 2) => {
    if (state.kind !== 'live') return;
    const canvas = canvasRef.current;
    if (!canvas || !canvas.width) return;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width / canvas.width, rect.height / canvas.height);
    if (!scale) return;
    const x = (event.clientX - rect.left - (rect.width - canvas.width * scale) / 2) / scale;
    const y = (event.clientY - rect.top - (rect.height - canvas.height * scale) / 2) / scale;
    if (action === 0 && (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height)) { pointerDown.current = false; return; }
    send({ type: 'touch', action, width: canvas.width, height: canvas.height,
      x: Math.round(Math.max(0, Math.min(canvas.width - 1, x))),
      y: Math.round(Math.max(0, Math.min(canvas.height - 1, y))) });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLCanvasElement>) => {
    if (state.kind !== 'live') return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (KEYCODES[event.key] !== undefined) send({ type: 'key', keycode: KEYCODES[event.key] });
    else if (event.key.length === 1) send({ type: 'text', text: event.key });
    else return;
    event.preventDefault();
  };

  return <div className={`flex min-h-0 min-w-0 flex-1 flex-col gap-3 ${screenOnly ? '' : 'p-4'}`}>
    {!screenOnly && <div className="flex items-center gap-2 text-xs text-zinc-400">
      <span className="min-w-0 flex-1 truncate">{state.kind === 'live' ? `Live · ${userName}` : userName}</span>
      <IconButton aria-label="Close phone screen" onClick={() => { stop(); setState({ kind: 'idle' }); onClose?.(); }}><X className="h-4 w-4" /></IconButton>
    </div>}
    <div className="flex min-h-0 w-full flex-1 items-center justify-center [container-type:size]" aria-busy={state.kind === 'connecting'}>
      <div className="relative overflow-hidden rounded-[1.75rem] bg-black ring-4 ring-inset ring-zinc-800"
        style={{ width: `min(100cqw, calc(100cqh * ${screenRatio}))`, aspectRatio: screenRatio }}>
        <canvas ref={canvasRef} tabIndex={state.kind === 'live' ? 0 : -1}
          aria-label={`Screen of ${userName}’s phone. Click to tap, type to enter text.`}
          className="absolute inset-1 h-[calc(100%-0.5rem)] w-[calc(100%-0.5rem)] touch-none rounded-[1.5rem] object-contain outline-none focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500"
          onPointerDown={(event) => { if (event.button !== 0) return; pointerDown.current = true; event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.focus(); touch(event, 0); }}
          onPointerMove={(event) => { if (pointerDown.current) touch(event, 2); }}
          onPointerUp={(event) => { if (!pointerDown.current) return; pointerDown.current = false; touch(event, 1); }}
          onPointerCancel={(event) => { if (!pointerDown.current) return; pointerDown.current = false; touch(event, 1); }}
          onKeyDown={onKeyDown}
          onPaste={(event) => { const text = event.clipboardData.getData('text'); if (state.kind === 'live' && text) send({ type: 'text', text: text.slice(0, 100) }); event.preventDefault(); }} />
        {state.kind !== 'live' && <div className="absolute inset-1 flex flex-col items-center justify-center gap-4 rounded-[1.5rem] bg-black px-6 text-center">
          {state.kind === 'connecting' ? <Spinner label="Connecting to phone screen" className="h-7 w-7 text-blue-400" />
            : <MonitorSmartphone className="h-7 w-7 text-zinc-400" />}
          <p role={state.kind === 'error' ? 'alert' : 'status'} className="max-w-xs text-sm text-zinc-200">
            {state.kind === 'idle' ? `View ${userName}’s screen` : state.kind === 'asleep' ? 'The phone’s screen is off.' : state.message}
          </p>
          {state.kind === 'asleep' && <Button size="sm" onClick={() => send({ type: 'key', keycode: KEY_WAKEUP })}>Wake phone</Button>}
          {(state.kind === 'idle' || state.kind === 'error') && <Button size="sm" onClick={start}>{state.kind === 'error' ? 'Reconnect' : 'View screen'}</Button>}
        </div>}
      </div>
    </div>
    <div className="flex shrink-0 items-center justify-center gap-6" role="toolbar" aria-label="Phone buttons">
      <IconButton aria-label="Back" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_BACK })}><ArrowLeft className="h-4 w-4" /></IconButton>
      <IconButton aria-label="Home" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_HOME })}><Circle className="h-4 w-4" /></IconButton>
      <IconButton aria-label="Recent apps" disabled={state.kind !== 'live'} onClick={() => send({ type: 'key', keycode: KEY_RECENT })}><Square className="h-3.5 w-3.5" /></IconButton>
    </div>
  </div>;
}
