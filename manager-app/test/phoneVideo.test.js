import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/devices/phoneVideo.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { h264DecoderConfig, h264Frame } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const sps = [0x67, 0x64, 0, 0x20, 0xac, 0xb4];
const pps = [0x68, 0xee, 0x3c, 0x80];
const config = new Uint8Array([0, 0, 0, 1, ...sps, 0, 0, 1, ...pps]);

test('provides AVC configuration with the phone profile and SPS/PPS', () => {
  const result = h264DecoderConfig(config);
  assert.equal(result.codec, 'avc1.640020');
  assert.equal(result.optimizeForLatency, true);
  assert.deepEqual([...result.description], [1, 0x64, 0, 0x20, 0xff, 0xe1, 0, sps.length, ...sps, 1, 0, pps.length, ...pps]);
});

test('converts mixed Annex B prefixes and all slices without altering NAL data', () => {
  const data = new Uint8Array([0, 0, 0, 1, 0x65, 0xaa, 0, 0, 3, 1, 0, 0, 1, 0x65, 0xbb]);
  const original = data.slice();
  assert.deepEqual([...h264Frame(data)], [0, 0, 0, 6, 0x65, 0xaa, 0, 0, 3, 1, 0, 0, 0, 2, 0x65, 0xbb]);
  assert.deepEqual(data, original);
});

test('keeps NAL lengths above 255 intact', () => {
  const frame = h264Frame(new Uint8Array([0, 0, 1, 0x65, ...new Array(300).fill(0xaa)]));
  assert.equal(new DataView(frame.buffer).getUint32(0), 301);
  assert.equal(frame.length, 305);
});

test('a new config retains changed parameter sets even with the same codec', () => {
  const changed = config.slice();
  changed[changed.length - 1] = 0x81;
  const first = h264DecoderConfig(config);
  const next = h264DecoderConfig(changed);
  assert.equal(first.codec, next.codec);
  assert.notDeepEqual(first.description, next.description);
});

test('rejects missing parameter sets and malformed video packets', () => {
  for (const data of [new Uint8Array(), new Uint8Array([0, 0, 1]), new Uint8Array([1, 2, 3])]) {
    assert.throws(() => h264Frame(data), /Invalid phone video/);
  }
  assert.throws(() => h264DecoderConfig(new Uint8Array([0, 0, 1, ...sps])), /incomplete H.264/);
  assert.throws(() => h264DecoderConfig(new Uint8Array([0, 0, 1, 0x67, 0, 0, 1, ...pps])), /incomplete H.264/);
});
