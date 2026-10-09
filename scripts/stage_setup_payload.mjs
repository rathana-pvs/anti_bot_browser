import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, '..');
const payloadRoot = join(projectRoot, 'manager-app', 'src-tauri', 'resources', 'payload');

const excludedNames = new Set([
  '.git',
  '.venv',
  '__pycache__',
  'automation_evidence',
  'build',
  'chrome_data',
  'dist',
  'node_modules',
  'reports',
  'staging',
  'target',
  'venv',
]);

const shouldCopy = (source) => {
  const name = source.split(/[\\/]/).pop() ?? '';
  if (excludedNames.has(name)) return false;
  return !name.endsWith('.pyc') && !name.endsWith('.log') && !name.endsWith('.pth');
};

rmSync(payloadRoot, { recursive: true, force: true });
mkdirSync(payloadRoot, { recursive: true });
writeFileSync(join(payloadRoot, '.gitkeep'), '');

const entries = [
  'install.sh',
  'install-windows.ps1',
  'VERSION',
  'automation',
  'backend',
  'container',
  'scripts/build_container.sh',
  'scripts/copy_runtime_payload.sh',
  'scripts/run_profile.sh',
];

for (const entry of entries) {
  const source = join(projectRoot, entry);
  if (!existsSync(source)) throw new Error(`Required setup payload is missing: ${entry}`);
  cpSync(source, join(payloadRoot, entry), { recursive: true, filter: shouldCopy });
}

console.log(`Staged desktop setup payload at ${payloadRoot}`);
