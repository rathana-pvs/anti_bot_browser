import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export function parseVersion(value) {
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/.exec(value);
  if (!match || (match[4] || '').split('.').some(part => /^\d+$/.test(part) && part.length > 1 && part[0] === '0')) throw new Error(`Invalid release version: ${value}`);
  return match.slice(1, 4).map(BigInt);
}
export function compareVersions(a, b) {
  const aa = parseVersion(a); const bb = parseVersion(b);
  for (let i = 0; i < 3; i++) if (aa[i] !== bb[i]) return aa[i] > bb[i] ? 1 : -1;
  const ap = a.split('-').slice(1).join('-'); const bp = b.split('-').slice(1).join('-');
  if (ap === bp) return 0;
  if (!ap || !bp) return !ap ? 1 : -1;
  const ax = ap.split('.'); const bx = bp.split('.');
  for (let i = 0; i < Math.max(ax.length, bx.length); i++) {
    if (ax[i] === undefined || bx[i] === undefined) return ax[i] === undefined ? -1 : 1;
    if (ax[i] === bx[i]) continue;
    const an = /^\d+$/.test(ax[i]); const bn = /^\d+$/.test(bx[i]);
    if (an !== bn) return an ? -1 : 1;
    return an ? (BigInt(ax[i]) > BigInt(bx[i]) ? 1 : -1) : (ax[i] > bx[i] ? 1 : -1);
  }
  return 0;
}
export function checkVersion(project = root, expected) {
  const read = path => readFileSync(resolve(project, path), 'utf8');
  const version = read('VERSION').trim(); parseVersion(version);
  const pkg = JSON.parse(read('manager-app/package.json'));
  const lock = JSON.parse(read('manager-app/package-lock.json'));
  const tauri = JSON.parse(read('manager-app/src-tauri/tauri.conf.json'));
  const versions = [pkg.version, lock.version, lock.packages[''].version, tauri.version,
    ...['Cargo.toml', 'Cargo.lock'].map(file => /name = "isolated-browser-manager"\s+version = "([^"]+)"/.exec(read(`manager-app/src-tauri/${file}`))?.[1])];
  if (versions.some(v => v !== version) || (expected && expected !== version)) throw new Error('VERSION, npm/Tauri/Cargo manifests, lockfiles and release tag must agree.');
  return version;
}
export function setVersion(version, project = root) {
  parseVersion(version);
  const current = checkVersion(project);
  if (compareVersions(version, current) <= 0) throw new Error(`Version must increase from ${current}`);
  for (const file of ['manager-app/package.json', 'manager-app/package-lock.json', 'manager-app/src-tauri/tauri.conf.json']) {
    const path = resolve(project, file); const data = JSON.parse(readFileSync(path, 'utf8'));
    data.version = version;
    if (data.packages) data.packages[''].version = version;
    writeFileSync(path, JSON.stringify(data, null, 2) + '\n');
  }
  for (const file of ['Cargo.toml', 'Cargo.lock']) {
    const path = resolve(project, `manager-app/src-tauri/${file}`);
    writeFileSync(path, readFileSync(path, 'utf8').replace(/(name = "isolated-browser-manager"\s+version = ")[^"]+("?)/, (_, prefix, suffix) => `${prefix}${version}${suffix}`));
  }
  writeFileSync(resolve(project, 'VERSION'), version + '\n');
  return checkVersion(project, version);
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [command, value] = process.argv.slice(2);
    if (!['check', 'set'].includes(command) || (command === 'set' && !value)) throw new Error('Usage: node scripts/release-version.mjs check [tag] | set VERSION');
    console.log(command === 'set' ? setVersion(value) : checkVersion(root, value?.replace(/^v/, '')));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
