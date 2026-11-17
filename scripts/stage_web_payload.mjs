import { cpSync, existsSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Runs after `npm run build`: ships the built web UI in the runtime payload so
// Settings > Web access can serve it from installed copies of the app.
const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(projectRoot, 'manager-app', 'dist');
const target = join(projectRoot, 'manager-app', 'src-tauri', 'resources', 'payload', 'web');

if (!existsSync(join(dist, 'index.html'))) throw new Error('Build the web app first: npm run build');
rmSync(target, { recursive: true, force: true });
cpSync(dist, target, { recursive: true });
console.log(`Staged web app at ${target}`);
