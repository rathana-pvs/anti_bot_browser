/** scrcpy sends Annex B; WebKit's H.264 decoder needs AVC configuration and length-prefixed NALs. */
function nalUnits(data: Uint8Array): Uint8Array[] {
  const starts: { prefix: number; nal: number }[] = [];
  for (let i = 0; i + 2 < data.length; i++) {
    if (data[i] !== 0 || data[i + 1] !== 0) continue;
    const length = data[i + 2] === 1 ? 3 : data[i + 2] === 0 && data[i + 3] === 1 ? 4 : 0;
    if (!length) continue;
    starts.push({ prefix: i, nal: i + length });
    i += length - 1;
  }
  const units = starts.map((start, index) => data.subarray(start.nal, starts[index + 1]?.prefix ?? data.length));
  if (!units.length || units.some(unit => !unit.length)) throw new Error('Invalid phone video packet');
  return units;
}

export function h264DecoderConfig(data: Uint8Array): VideoDecoderConfig {
  const units = nalUnits(data);
  const sps = units.filter(unit => (unit[0] & 0x1f) === 7);
  const pps = units.filter(unit => (unit[0] & 0x1f) === 8);
  if (!sps.length || !pps.length || sps[0].length < 4 || sps.length > 31 || pps.length > 255
      || [...sps, ...pps].some(unit => unit.length > 0xffff)) {
    throw new Error('The phone sent incomplete H.264 configuration');
  }
  const profile = sps[0].subarray(1, 4);
  const codec = `avc1.${Array.from(profile, byte => byte.toString(16).padStart(2, '0')).join('')}`;
  // AVCDecoderConfigurationRecord, with four-byte NAL lengths and the phone's SPS/PPS.
  const description = new Uint8Array(7 + [...sps, ...pps].reduce((size, unit) => size + 2 + unit.length, 0));
  description.set([1, ...profile, 0xff, 0xe0 | sps.length]);
  const view = new DataView(description.buffer);
  let offset = 6;
  for (const group of [sps, pps]) {
    if (group === pps) description[offset++] = pps.length;
    for (const unit of group) {
      view.setUint16(offset, unit.length);
      description.set(unit, offset + 2);
      offset += 2 + unit.length;
    }
  }
  return { codec, description, optimizeForLatency: true };
}

export function h264Frame(data: Uint8Array): Uint8Array {
  const units = nalUnits(data);
  const frame = new Uint8Array(units.reduce((size, unit) => size + 4 + unit.length, 0));
  const view = new DataView(frame.buffer);
  let offset = 0;
  for (const unit of units) {
    view.setUint32(offset, unit.length);
    frame.set(unit, offset + 4);
    offset += 4 + unit.length;
  }
  return frame;
}
