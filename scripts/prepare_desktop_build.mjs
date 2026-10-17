import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { checkVersion } from './release-version.mjs';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, '..');
checkVersion(projectRoot);

const backendVersion = readFileSync(join(projectRoot, 'VERSION'), 'utf8').trim();
const packageVersion = JSON.parse(
  readFileSync(join(projectRoot, 'manager-app', 'package.json'), 'utf8'),
).version;
const tauriVersion = JSON.parse(
  readFileSync(join(projectRoot, 'manager-app', 'src-tauri', 'tauri.conf.json'), 'utf8'),
).version;

if (!backendVersion || backendVersion !== packageVersion || backendVersion !== tauriVersion) {
  throw new Error(
    `Build versions must match: VERSION=${backendVersion || '<empty>'}, `
    + `package.json=${packageVersion}, tauri.conf.json=${tauriVersion}`,
  );
}

// Linux packages launch the frozen FastAPI sidecar in preference to the
// Python runtime. Rebuild it for every desktop package so the frontend and
// backend can never come from different revisions. Windows uses the backend
// installed in WSL and therefore does not bundle this Linux executable.
if (process.platform !== 'win32') {
  const sidecarBuild = spawnSync('bash', [join(projectRoot, 'backend', 'build_sidecar.sh')], {
    cwd: projectRoot,
    stdio: 'inherit',
  });
  if (sidecarBuild.error) throw sidecarBuild.error;
  if (sidecarBuild.status !== 0) {
    throw new Error(`Backend sidecar build failed with exit code ${sidecarBuild.status}`);
  }
}

await import(pathToFileURL(join(scriptDirectory, 'stage_setup_payload.mjs')).href);
const privacyScan = spawnSync(process.execPath, [join(scriptDirectory, 'scan_release_for_secrets.mjs'),
  projectRoot, join(projectRoot, 'manager-app/src-tauri/resources/payload')], { cwd: projectRoot, stdio: 'inherit' });
if (privacyScan.error) throw privacyScan.error;
if (privacyScan.status !== 0) throw new Error('Setup payload failed release privacy checks.');
