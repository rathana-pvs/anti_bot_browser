import { readdirSync, readFileSync, mkdirSync, copyFileSync, writeFileSync } from 'node:fs';
import { resolve, join, basename } from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { checkVersion, root } from './release-version.mjs';
import { verifySignedAsset } from './verify-updater-asset.mjs';

export function buildManifest(version, assets, repository, notes = '') {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid release repository');
  const types = { '.deb': 'linux-x86_64-deb', '.AppImage': 'linux-x86_64-appimage' };
  const platforms = {};
  for (const { name, signature } of assets) {
    const suffix = Object.keys(types).find(ext => name.endsWith(ext));
    if (!suffix) continue;
    if (basename(name) !== name || !signature?.trim()) throw new Error('Invalid asset/signature');
    if (platforms[types[suffix]]) throw new Error(`Duplicate platform artifact: ${types[suffix]}`);
    platforms[types[suffix]] = { url: `https://github.com/${repository}/releases/download/v${version}/${encodeURIComponent(name)}`, signature: signature.trim() };
  }
  for (const target of Object.values(types)) if (!platforms[target]) throw new Error(`Missing signed artifact: ${target}`);
  return { version, notes, pub_date: new Date().toISOString(), platforms };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const input = resolve(process.argv[2]); const output = resolve(process.argv[3]);
  const version = checkVersion(root, process.env.RELEASE_TAG?.replace(/^v/, ''));
  const walk = dir => readdirSync(dir, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? walk(join(dir, entry.name)) : [join(dir, entry.name)]);
  const paths = walk(input).filter(p => /\.(deb|AppImage)$/.test(p));
  const assets = paths.map(path => ({ name: basename(path), signature: readFileSync(path + '.sig', 'utf8') }));
  const publicKey = JSON.parse(readFileSync(join(root,'manager-app/src-tauri/tauri.conf.json'),'utf8')).plugins.updater.pubkey;
  for (let i=0; i<paths.length; i++) verifySignedAsset(paths[i], assets[i].signature, publicKey, version);
  const manifest = buildManifest(version, assets, process.env.RELEASE_REPOSITORY, readFileSync(join(root,'RELEASE_NOTES.md'),'utf8'));
  mkdirSync(output, { recursive: true });
  for (const path of paths) { copyFileSync(path, join(output, basename(path))); copyFileSync(path+'.sig', join(output, basename(path)+'.sig')); }
  writeFileSync(join(output,'latest.json'), JSON.stringify(manifest,null,2)+'\n');
  const sums = readdirSync(output).filter(name=>name !== 'SHA256SUMS').sort().map(name => `${createHash('sha256').update(readFileSync(join(output,name))).digest('hex')}  ${name}`);
  writeFileSync(join(output,'SHA256SUMS'), sums.join('\n')+'\n');
  console.log(`Assembled signed release v${version} for ${Object.keys(manifest.platforms).join(', ')}`);
}
