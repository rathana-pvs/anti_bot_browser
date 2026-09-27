import fs from 'fs';
import os from 'os';
import path from 'path';

const [sourceRootArg, releaseRootArg] = process.argv.slice(2);
if (!sourceRootArg || !releaseRootArg) {
  console.error('Usage: node scan_release_for_secrets.mjs <source-root> <release-root>');
  process.exit(2);
}

const sourceRoot = path.resolve(sourceRootArg);
const releaseRoot = path.resolve(releaseRootArg);
const secretKeys = new Set([
  'proxy_host', 'proxy_user', 'proxy_pass', 'username', 'password',
  'email', 'access_token', 'refresh_token', 'cookie', 'secret',
]);

function collectSensitiveValues(value, output) {
  if (Array.isArray(value)) {
    for (const item of value) collectSensitiveValues(item, output);
    return;
  }
  if (!value || typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value)) {
    if (secretKeys.has(key.toLowerCase()) && (typeof child === 'string' || typeof child === 'number')) {
      const text = String(child).trim();
      if (text.length >= 6) output.add(text);
    }
    collectSensitiveValues(child, output);
  }
}

function readJsonIfPresent(filename, output) {
  try {
    collectSensitiveValues(JSON.parse(fs.readFileSync(filename, 'utf8')), output);
  } catch (_) {}
}

const sensitiveValues = new Set();
const profilesDir = path.join(sourceRoot, 'profiles');
if (fs.existsSync(profilesDir)) {
  for (const entry of fs.readdirSync(profilesDir, { withFileTypes: true })) {
    if (entry.isDirectory()) readJsonIfPresent(path.join(profilesDir, entry.name, 'config.json'), sensitiveValues);
  }
}
readJsonIfPresent(path.join(sourceRoot, 'proxies', 'proxy_pool.json'), sensitiveValues);

const forbiddenPathParts = new Set([
  'chrome_data', 'automation_evidence', 'node_modules', 'venv', '__pycache__', '.git',
]);
const textExtensions = new Set([
  '.js', '.jsx', '.mjs', '.ts', '.tsx', '.json', '.md', '.txt', '.yaml', '.yml',
  '.py', '.sh', '.toml', '.html', '.css', '.conf', '.example',
]);
let scannedFiles = 0;
const violations = [];

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    const relative = path.relative(releaseRoot, fullPath);
    if (relative.split(path.sep).some((part) => forbiddenPathParts.has(part))) {
      violations.push(`${relative}: forbidden private/cache directory`);
      continue;
    }
    if (entry.isDirectory()) {
      walk(fullPath);
      continue;
    }
    if (!entry.isFile() || !textExtensions.has(path.extname(entry.name).toLowerCase())) continue;
    const stat = fs.statSync(fullPath);
    if (stat.size > 5 * 1024 * 1024) continue;
    const text = fs.readFileSync(fullPath, 'utf8');
    scannedFiles += 1;
    if (text.includes(sourceRoot) || text.includes(os.homedir())) {
      violations.push(`${relative}: contains a developer-local filesystem path`);
      continue;
    }
    for (const secret of sensitiveValues) {
      if (text.includes(secret)) {
        violations.push(`${relative}: matches an active sensitive value`);
        break;
      }
    }
  }
}

walk(releaseRoot);
if (violations.length > 0) {
  console.error('Release privacy scan failed:');
  for (const violation of violations) console.error(`- ${violation}`);
  process.exit(1);
}
console.log(`Release privacy scan passed (${scannedFiles} text files; ${sensitiveValues.size} active values checked).`);
