// CI uses the standard minisign verifier, not a custom cryptographic verifier.
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

export function verifySignedAsset(path, encodedSignature, encodedPublicKey, version, executable = 'minisign') {
  const dir = mkdtempSync(join(tmpdir(), 'verify-updater-'));
  try {
    const signature = Buffer.from(encodedSignature.trim(), 'base64').toString('utf8');
    const publicKey = Buffer.from(encodedPublicKey.trim(), 'base64').toString('utf8');
    const sigPath = join(dir, 'artifact.sig'); const keyPath = join(dir, 'public.key');
    writeFileSync(sigPath, signature); writeFileSync(keyPath, publicKey);
    execFileSync(executable, ['-Vm', path, '-p', keyPath, '-x', sigPath, '-q'], { stdio: 'pipe' });
    const trusted = signature.split(/\r?\n/)[2] || '';
    if (!trusted.startsWith('trusted comment: ') || !trusted.slice(17).split('\t').includes(`version:${version}`)) {
      throw new Error('Artifact signature is not bound to the announced release version.');
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
}
