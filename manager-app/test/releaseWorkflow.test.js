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

const assets=['app.deb','app.AppImage'].map(name=>({name,signature:'signed-test-fixture'}));
test('one release manifest includes both Linux formats and nothing else',()=>{
  const manifest=buildManifest('1.1.0',assets,'owner/repo','Notes');
  assert.equal(Object.keys(manifest.platforms).length,2);
  assert.deepEqual(Object.keys(buildManifest('1.1.0',[...assets,{name:'app.exe',signature:'x'}],'owner/repo').platforms).sort(),['linux-x86_64-appimage','linux-x86_64-deb']);
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
  assert.match(workflow,/python -m pytest backend\/tests -q/);
  assert.match(workflow,/python -m unittest discover -s tests/);
});

test('desktop settings keep the updater navigation and guarded native commands wired',()=>{
  const settings=readFileSync(join(root,'manager-app/src/components/SettingsModal.tsx'),'utf8');
  assert.match(settings,/import \{ AppUpdates \} from '\.\/AppUpdates'/);
  assert.match(settings,/setSection\('updates'\)/);
  assert.match(settings,/<AppUpdates\s*\/>/);
  const native=readFileSync(join(root,'manager-app/src-tauri/src/main.rs'),'utf8');
  for(const command of ['check_app_update','download_app_update','install_app_update']) {
    assert.match(native,new RegExp(`app_updates::${command}`));
  }
  assert.match(native,/tauri_plugin_updater::Builder/);
});

test('Python CI workflows install the shared offline test dependencies',()=>{
  const requirements=readFileSync(join(root,'requirements-test.txt'),'utf8');
  for(const dependency of ['-r backend/requirements.txt','numpy>=','opencv-python-headless>=','PyYAML>=']) {
    assert.ok(requirements.includes(dependency),`Missing test dependency: ${dependency}`);
  }
  for(const file of ['test.yml','build-desktop.yml','reliability-drill.yml']) {
    const workflow=readFileSync(join(root,'.github/workflows',file),'utf8');
    assert.match(workflow,/python -m pip install -r requirements-test\.txt/);
  }
});

test('media tests have FFmpeg installed in backend and release CI',()=>{
  for (const file of ['test.yml','build-desktop.yml']) {
    const workflow=readFileSync(join(root,'.github/workflows',file),'utf8');
    assert.match(workflow,/apt-get install -y [^\n]*\bffmpeg\b/);
    assert.ok(workflow.indexOf(' ffmpeg') < workflow.indexOf('python -m pytest backend/tests -q'));
  }
});

test('backend CI receives the tested dashboard before importing the application',()=>{
  const workflow=readFileSync(join(root,'.github/workflows/test.yml'),'utf8');
  const [frontend,python]=workflow.split('\n  python:');
  assert.match(frontend,/npm run build[\s\S]*uses: actions\/upload-artifact@v4/);
  assert.match(frontend,/name: tested-dashboard\s+path: manager-app\/dist\//);
  assert.match(frontend,/if-no-files-found: error/);
  assert.match(python,/needs: frontend/);
  assert.match(python,/uses: actions\/download-artifact@v4[\s\S]*name: tested-dashboard\s+path: manager-app\/dist\//);
  assert.match(python,/test -s manager-app\/dist\/index\.html/);
  assert.ok(python.indexOf('test -s manager-app/dist/index.html') < python.indexOf('python -m pytest backend/tests -q'));
});
