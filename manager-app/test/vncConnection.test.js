import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/vncConnection.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { startVncConnection } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

function setup(t, { throws = false } = {}) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const clients = [];
  const statuses = [];
  let active;
  const stop = startVncConnection({
    createClient() {
      if (throws) throw new Error('Unavailable');
      const listeners = new Map();
      const client = {
        closed: false,
        addEventListener: (type, handler) => listeners.set(type, handler),
        emit: (type) => listeners.get(type)?.(),
        disconnect() { this.closed = true; this.emit('disconnect'); },
      };
      clients.push(client);
      return client;
    },
    onClient: (client) => { active = client; },
    onStatus: (status, retries) => statuses.push({ status, retries }),
  });
  t.after(stop);
  return { clients, statuses, stop, active: () => active };
}

test('a stalled RFB handshake times out and reconnects', (t) => {
  const h = setup(t);
  t.mock.timers.tick(10000);
  assert.equal(h.clients[0].closed, true);
  assert.deepEqual(h.statuses.at(-1), { status: 'connecting', retries: 1 });
  t.mock.timers.tick(750);
  assert.equal(h.clients.length, 2);
  h.clients[1].emit('connect');
  t.mock.timers.tick(10000);
  assert.deepEqual(h.statuses.at(-1), { status: 'connected', retries: 0 });
  assert.equal(h.active(), h.clients[1]);
});

test('late events from an old socket cannot clear or replace a new connection', (t) => {
  const h = setup(t);
  h.clients[0].emit('disconnect');
  t.mock.timers.tick(750);
  h.clients[1].emit('connect');
  const statusCount = h.statuses.length;
  h.clients[0].emit('disconnect');
  h.clients[0].emit('securityfailure');
  h.clients[0].emit('connect');
  t.mock.timers.tick(20000);
  assert.equal(h.statuses.length, statusCount);
  assert.equal(h.clients.length, 2);
  assert.equal(h.active(), h.clients[1]);
});

test('switching profiles cancels both pending deadlines and reconnects', (t) => {
  const h = setup(t);
  h.clients[0].emit('disconnect');
  h.stop();
  const statusCount = h.statuses.length;
  t.mock.timers.tick(60000);
  h.clients[0].emit('connect');
  assert.equal(h.clients.length, 1);
  assert.equal(h.statuses.length, statusCount);
  assert.equal(h.active(), null);
});

test('constructor failures stop after the retry budget is exhausted', (t) => {
  const h = setup(t, { throws: true });
  for (let retry = 1; retry <= 20; retry += 1) {
    t.mock.timers.tick(Math.min(3000, 600 + retry * 150));
  }
  assert.deepEqual(h.statuses.at(-1), { status: 'disconnected', retries: 20 });
  const statusCount = h.statuses.length;
  t.mock.timers.tick(60000);
  assert.equal(h.statuses.length, statusCount);
});

test('cleanup while a handshake is pending cancels its deadline', (t) => {
  const h = setup(t);
  h.stop();
  const statusCount = h.statuses.length;
  t.mock.timers.tick(60000);
  assert.equal(h.clients[0].closed, true);
  assert.equal(h.clients.length, 1);
  assert.equal(h.statuses.length, statusCount);
});

test('a connected socket that drops recovers and security failure schedules only one retry', (t) => {
  const h = setup(t);
  h.clients[0].emit('connect');
  h.clients[0].emit('securityfailure');
  h.clients[0].emit('disconnect');
  t.mock.timers.tick(750);
  assert.equal(h.clients.length, 2);
  assert.deepEqual(h.statuses.at(-1), { status: 'connecting', retries: 1 });
});
