import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import path from 'path';
import os from 'os';
import {
  parseRunTimestamp,
  calculateDirectorySizeBytes,
  formatBytes,
  cleanProfileEvidence,
  cleanAllProfilesEvidence,
} from '../evidenceCleanup.js';

test('parseRunTimestamp extracts UTC timestamp from standard run directory name', () => {
  const folderName = '20260925T130155_196828Z_FacebookPostTask';
  const ts = parseRunTimestamp(folderName);
  assert.ok(ts !== null);
  const date = new Date(ts);
  assert.equal(date.getUTCFullYear(), 2026);
  assert.equal(date.getUTCMonth(), 8); // 0-indexed: 8 is September
  assert.equal(date.getUTCDate(), 25);
  assert.equal(date.getUTCHours(), 13);
  assert.equal(date.getUTCMinutes(), 1);
  assert.equal(date.getUTCSeconds(), 55);
});

test('formatBytes formats various sizes correctly', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(1024), '1.0 KiB');
  assert.equal(formatBytes(1024 * 1024 * 15.5), '15.5 MiB');
  assert.equal(formatBytes(1024 * 1024 * 1024 * 2.2), '2.2 GiB');
});

test('cleanProfileEvidence keeps last 3 days and deletes older evidence', () => {
  const tmpBase = fs.mkdtempSync(path.join(os.tmpdir(), 'evidence-test-'));
  const profileDir = path.join(tmpBase, 'profile_test');
  const evidenceDir = path.join(profileDir, 'automation_evidence');
  fs.mkdirSync(evidenceDir, { recursive: true });

  const now = new Date('2026-09-26T12:00:00Z').getTime();

  // Run 1: 5 days old (2026-09-21) -> Should be DELETED
  const oldRunName = '20260921T120000_123456Z_FacebookPostTask';
  const oldRunDir = path.join(evidenceDir, oldRunName);
  fs.mkdirSync(oldRunDir);
  fs.writeFileSync(path.join(oldRunDir, 'screen.png'), Buffer.alloc(1024 * 50)); // 50 KB
  fs.writeFileSync(path.join(oldRunDir, 'result.json'), JSON.stringify({ status: 'published' }));

  // Run 2: 2 days old (2026-09-24) -> Should be KEPT
  const midRunName = '20260924T120000_123456Z_FacebookPostTask';
  const midRunDir = path.join(evidenceDir, midRunName);
  fs.mkdirSync(midRunDir);
  fs.writeFileSync(path.join(midRunDir, 'screen.png'), Buffer.alloc(1024 * 20));
  fs.writeFileSync(path.join(midRunDir, 'result.json'), JSON.stringify({ status: 'published' }));

  // Run 3: 1 day old (2026-09-25) -> Should be KEPT
  const recentRunName = '20260925T120000_123456Z_FacebookWarmingTask';
  const recentRunDir = path.join(evidenceDir, recentRunName);
  fs.mkdirSync(recentRunDir);
  fs.writeFileSync(path.join(recentRunDir, 'screen.png'), Buffer.alloc(1024 * 10));

  try {
    const result = cleanProfileEvidence(profileDir, 3, now);

    assert.equal(result.deleted_runs, 1);
    assert.equal(result.kept_runs, 2);
    assert.ok(result.freed_bytes >= 1024 * 50);

    // Verify disk state
    assert.equal(fs.existsSync(oldRunDir), false, 'Old run should be deleted');
    assert.equal(fs.existsSync(midRunDir), true, '2-day run should be kept');
    assert.equal(fs.existsSync(recentRunDir), true, '1-day run should be kept');
  } finally {
    fs.rmSync(tmpBase, { recursive: true, force: true });
  }
});

test('cleanAllProfilesEvidence aggregates across all profiles', () => {
  const tmpBase = fs.mkdtempSync(path.join(os.tmpdir(), 'evidence-all-test-'));
  const prof1 = path.join(tmpBase, 'profile_001');
  const prof2 = path.join(tmpBase, 'profile_002');
  const shared = path.join(tmpBase, 'shared_media');

  fs.mkdirSync(path.join(prof1, 'automation_evidence'), { recursive: true });
  fs.mkdirSync(path.join(prof2, 'automation_evidence'), { recursive: true });
  fs.mkdirSync(shared, { recursive: true });

  const now = new Date('2026-09-26T12:00:00Z').getTime();

  // Prof1 has an old run (4 days old)
  const oldRun1 = path.join(prof1, 'automation_evidence', '20260922T100000_111Z_Task');
  fs.mkdirSync(oldRun1);
  fs.writeFileSync(path.join(oldRun1, 'data.bin'), Buffer.alloc(1024 * 100));

  // Prof2 has an old run (5 days old) and a new run (1 day old)
  const oldRun2 = path.join(prof2, 'automation_evidence', '20260921T100000_222Z_Task');
  const newRun2 = path.join(prof2, 'automation_evidence', '20260925T100000_333Z_Task');
  fs.mkdirSync(oldRun2);
  fs.mkdirSync(newRun2);
  fs.writeFileSync(path.join(oldRun2, 'data.bin'), Buffer.alloc(1024 * 100));
  fs.writeFileSync(path.join(newRun2, 'data.bin'), Buffer.alloc(1024 * 100));

  try {
    const res = cleanAllProfilesEvidence(tmpBase, 3, now);
    assert.equal(res.total_deleted_runs, 2);
    assert.equal(res.total_kept_runs, 1);
    assert.ok(res.total_freed_bytes >= 1024 * 200);

    assert.equal(fs.existsSync(oldRun1), false);
    assert.equal(fs.existsSync(oldRun2), false);
    assert.equal(fs.existsSync(newRun2), true);
  } finally {
    fs.rmSync(tmpBase, { recursive: true, force: true });
  }
});
