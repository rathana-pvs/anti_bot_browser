import { execFileSync } from 'node:child_process';
import { checkVersion, compareVersions, parseVersion, root } from './release-version.mjs';
const tag = process.argv[2];
if (!tag?.startsWith('v')) throw new Error('Release tag must start with v.');
const version = checkVersion(root, tag.slice(1));
const tags = execFileSync('git', ['tag', '--list', 'v*'], { cwd: root, encoding: 'utf8' }).trim().split('\n');
for (const existing of tags) {
  if (!existing || existing === tag) continue;
  try { parseVersion(existing.slice(1)); } catch { continue; }
  if (compareVersions(version, existing.slice(1)) <= 0) throw new Error(`${tag} must be newer than existing ${existing}.`);
}
console.log(`Validated deliberate release ${tag}`);
