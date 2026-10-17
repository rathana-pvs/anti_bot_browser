import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { checkVersion, compareVersions, parseVersion, setVersion, root } from '../../scripts/release-version.mjs';
import { buildManifest } from '../../scripts/assemble-release.mjs';

test('release versions compare numerically, including prerelease identifiers', () => {
  assert.equal(compareVersions('1.0.19','1.0.2'),1);
  assert.equal(compareVersions('1.1.0','1.0.19'),1);
  assert.equal(compareVersions('1.1.0-beta.10','1.1.0-beta.2'),1);
  assert.equal(compareVersions('1.1.0','1.1.0-beta.10'),1);
  for (const value of ['v1.1.0','01.1.0','1.1','1.1.0-beta.01','1.1.0;echo bad']) assert.throws(()=>parseVersion(value));
});

test('one command synchronizes every version and rejects accidental downgrades', () => {
  const dir=mkdtempSync(join(tmpdir(),'release-version-'));
  try {
    mkdirSync(join(dir,'manager-app/src-tauri'),{recursive:true});
    const files=['VERSION','manager-app/package.json','manager-app/package-lock.json','manager-app/src-tauri/tauri.conf.json','manager-app/src-tauri/Cargo.toml','manager-app/src-tauri/Cargo.lock'];
    for (const file of files) writeFileSync(join(dir,file),readFileSync(join(root,file)));
    const current=checkVersion(dir);
    assert.throws(()=>setVersion('1.0.2',dir));
    assert.equal(setVersion('2.0.0',dir),'2.0.0');
    assert.equal(checkVersion(dir,'2.0.0'),'2.0.0');
    assert.throws(()=>checkVersion(dir,current));
    const file=join(dir,'manager-app/package.json');
    const pkg=JSON.parse(readFileSync(file)); pkg.version='3.0.0'; writeFileSync(file,JSON.stringify(pkg));
    assert.throws(()=>checkVersion(dir));
  } finally { rmSync(dir,{recursive:true,force:true}); }
});

const assets=['app.deb','app.AppImage','app.exe','app.msi'].map(name=>({name,signature:'signed-test-fixture'}));
test('one release manifest includes both Linux formats and both Windows installers',()=>{
  const manifest=buildManifest('1.1.0',assets,'owner/repo','Notes');
  assert.equal(Object.keys(manifest.platforms).length,4);
  assert.match(manifest.platforms['linux-x86_64-deb'].url,/\/v1\.1\.0\/app.deb$/);
  assert.equal(manifest.notes,'Notes');
  assert.throws(()=>buildManifest('1.1.0',assets.slice(1),'owner/repo'));
  assert.throws(()=>buildManifest('1.1.0',[...assets,assets[0]],'owner/repo'));
  assert.throws(()=>buildManifest('1.1.0',assets.map(a=>({...a,signature:''})),'owner/repo'));
});

test('tag builds create drafts; manual builds and ordinary pushes never release',()=>{
  const workflow=readFileSync(join(root,'.github/workflows/build-desktop.yml'),'utf8');
  assert.match(workflow,/tags: \['v\*'\]/);
  assert.match(workflow,/needs: build/);
  assert.match(workflow,/gh release create .*--draft/);
  assert.match(workflow,/Release is already published; refusing to replace it/);
  assert.doesNotMatch(workflow,/gh release edit .*--draft=false|branches: \[.*main/);
});
