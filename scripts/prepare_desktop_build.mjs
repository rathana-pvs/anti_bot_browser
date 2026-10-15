import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, '..');

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
