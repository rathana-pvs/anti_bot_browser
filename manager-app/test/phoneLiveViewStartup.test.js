import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/devices/PhoneLiveView.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} });

function mount(autoStart) {
  const effects = [], timers = new Map(), sockets = [], states = [], decoders = [];
  let timerId = 0;
  const react = {
    useState: value => [value, next => states.push(next)],
    useRef: current => ({ current }),
    useCallback: fn => fn,
    useEffect: setup => effects.push(setup),
  };
  const exports = {};
  vm.runInNewContext(outputText, {
    exports,
    require: name => name === 'react' ? react
      : name === 'react/jsx-runtime' ? { jsx: () => null, jsxs: () => null }
      : name.endsWith('/services/devices') ? { deviceLiveUrl: () => 'ws://localhost/live' }
      : name.endsWith('/phoneVideo') ? { h264DecoderConfig: () => ({codec: 'avc1.640020'}), h264Frame: value => value } : {},
    window: {
      setTimeout: fn => { timers.set(++timerId, fn); return timerId; },
      clearTimeout: id => timers.delete(id),
    },
    VideoDecoder: class {
      state = 'unconfigured';
      constructor() { decoders.push(this); }
      static async isConfigSupported() { return { supported: true }; }
      configure() { this.state = 'configured'; }
      decode() {}
      close() { this.state = 'closed'; }
    },
    DOMException,
    EncodedVideoChunk: class {},
    WebSocket: class { static OPEN = 1; readyState = 1; constructor() { sockets.push(this); } close() { this.closed = true; } send() {} },
  });
  exports.PhoneLiveView({ runtime: { device_serial: 'PHONE', android_user_id: 0 }, autoStart });
  return { effects, timers, sockets, states, decoders, flush() {
    const pending = [...timers.values()];
    timers.clear();
    pending.forEach(fn => fn());
  } };
}

test('StrictMode effect replay opens one phone connection and cleans it up', () => {
  const view = mount(true);
  view.effects.map(setup => setup()).forEach(cleanup => cleanup?.());
  const cleanup = view.effects.map(setup => setup());
  assert.equal(view.sockets.length, 0);
  view.flush();
  assert.equal(view.sockets.length, 1);
  cleanup.forEach(fn => fn?.());
  assert.equal(view.sockets[0].closed, true);
});

test('closing the viewer before startup never contacts the phone', () => {
  const view = mount(true);
  view.effects.map(setup => setup()).forEach(cleanup => cleanup?.());
  view.flush();
  assert.equal(view.sockets.length, 0);
});

test('account viewers still wait for the user to start them', () => {
  const view = mount(false);
  view.effects.forEach(setup => setup());
  view.flush();
  assert.equal(view.sockets.length, 0);
});

test('a sleeping phone shows the screen-off state, not a live black picture', async () => {
  const view = mount(true);
  view.effects.forEach(setup => setup());
  view.flush();
  const socket = view.sockets[0];
  const sent = [];
  socket.send = message => sent.push(JSON.parse(message));
  const screen = async awake => {
    socket.onmessage({ data: JSON.stringify({ type: 'screen', awake }) });
    await new Promise(resolve => setImmediate(resolve));
  };
  await screen(false);
  assert.equal(view.states.at(-1).kind, 'asleep');
  await screen(true);
  assert.equal(view.states.at(-1).kind, 'connecting');
  assert.deepEqual(sent, [{ type: 'reset_video' }]);
});

// Exercise the actual connection handlers when a decoder accepts video but never outputs a frame.
test('a silent decoder retries in software, then shows a reconnect error', async () => {
  const view = mount(true);
  view.effects.forEach(setup => setup());
  view.flush();
  const socket = view.sockets[0];
  socket.onmessage({ data: JSON.stringify({ type: 'meta' }) });
  const config = new ArrayBuffer(13);
  const header = new DataView(config);
  header.setBigUint64(0, 1n << 63n);
  header.setUint32(8, 1);
  socket.onmessage({ data: config });
  // Drain the ordered message chain and asynchronous decoder support check.
  await new Promise(resolve => setImmediate(resolve));
  view.flush();
  assert.equal(view.decoders.length, 2);
  view.flush();
  assert.equal(view.states.at(-1).kind, 'error');
  assert.match(view.states.at(-1).message, /did not show a video frame/);
  assert.equal(socket.closed, true);
});
