import express from 'express';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { exec, execFile, execFileSync, execSync, spawn } from 'child_process';
import { randomUUID } from 'crypto';
import os from 'os';
import multer from 'multer';
import {
  loadProxyPool,
  saveProxyPool,
  importProxiesFromText,
  assignProxy,
  releaseProxy,
  syncProxyAssignment,
  deleteProxy,
  testProxyPing,
} from './proxyManager.js';
import {
  applyAutomationPermalinkResult,
  applyCommentRetryResult,
  applyCommentEvidenceBackfill,
  applyWarmingResult,
  applyVerifiedPermalinkBackfill,
  batchContainsUnresolvedExecution,
  classifyInterruptedExecution,
  canRetryFirstComment,
  isExecutionDeletionLocked,
  validateFacebookPermalink,
} from './queueSafety.js';
import { buildQueueTelemetrySummary } from './telemetrySummary.js';
import {
  countBufferedPreparations,
  orderedDueExecutions,
  shuffledProfileOrder,
} from './profilePipeline.js';
import {
  RESOURCE_MODE_LIMITS,
  recommendedOcrThreads,
  resolveOcrDevice,
  resolveResourceMode,
  resourceAdmissionDecision,
} from './resourceModes.js';
import {
  DEFAULT_SCHEDULER_CONFIG,
  canAcquireSchedulerSlot,
  claimExecutionLease,
  computeExecutionSchedule,
  findStaleRunningExecutions,
  isLeaseActive,
  refreshExecutionLease,
  releaseExecutionLease,
  schedulerSnapshot,
  workerSilenceTimeoutMs,
} from './queueScheduler.js';
import { cleanProfileEvidence, cleanAllProfilesEvidence } from './evidenceCleanup.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, '..');
const PROFILES_DIR = path.join(ROOT_DIR, 'profiles');
const SCRIPTS_DIR = path.join(ROOT_DIR, 'scripts');
const DATA_DIR = path.join(ROOT_DIR, 'data');
const SHARED_MEDIA_DIR = path.join(PROFILES_DIR, 'shared_media');
const QUEUE_FILE = path.join(DATA_DIR, 'posting_queue.json');
const QUEUE_CLAIM_LOCK_FILE = path.join(DATA_DIR, 'posting_queue.claim.lock');
const MANAGER_SETTINGS_FILE = path.join(DATA_DIR, 'manager_settings.json');

if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
if (!fs.existsSync(SHARED_MEDIA_DIR)) fs.mkdirSync(SHARED_MEDIA_DIR, { recursive: true });

function positiveInteger(value, fallback, minimum = 1) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed >= minimum ? parsed : fallback;
}

const configuredLeaseTtlMs = positiveInteger(
  process.env.AUTOMATION_LEASE_TTL_MS,
  DEFAULT_SCHEDULER_CONFIG.lease_ttl_ms,
  10_000,
);
const configuredHeartbeatMs = positiveInteger(
  process.env.AUTOMATION_HEARTBEAT_INTERVAL_MS,
  DEFAULT_SCHEDULER_CONFIG.heartbeat_interval_ms,
  2_000,
);
function loadManagerSettings() {
  try {
    if (fs.existsSync(MANAGER_SETTINGS_FILE)) {
      const parsed = JSON.parse(fs.readFileSync(MANAGER_SETTINGS_FILE, 'utf-8'));
      if (['auto', 'low', 'medium', 'high'].includes(parsed.resource_mode)) return parsed;
    }
  } catch (error) {
    console.error('Could not load manager settings:', error);
  }
  return { resource_mode: 'auto' };
}

function saveManagerSettings(settings) {
  const tmpFile = `${MANAGER_SETTINGS_FILE}.tmp`;
  fs.writeFileSync(tmpFile, JSON.stringify(settings, null, 2), 'utf-8');
  fs.renameSync(tmpFile, MANAGER_SETTINGS_FILE);
}

const hostTotalMemoryGb = os.totalmem() / (1024 ** 3);
let managerSettings = loadManagerSettings();
let resourceModeResolution = resolveResourceMode(managerSettings.resource_mode, hostTotalMemoryGb, os.cpus().length);
if (!resourceModeResolution.supported) {
  managerSettings = { resource_mode: 'auto', updated_at: new Date().toISOString() };
  resourceModeResolution = resolveResourceMode('auto', hostTotalMemoryGb, os.cpus().length);
  saveManagerSettings(managerSettings);
}

function buildSchedulerConfig() {
  return Object.freeze({
    ...RESOURCE_MODE_LIMITS[resourceModeResolution.effective],
    lease_ttl_ms: configuredLeaseTtlMs,
    heartbeat_interval_ms: Math.min(configuredHeartbeatMs, Math.floor(configuredLeaseTtlMs / 2)),
  });
}

let SCHEDULER_CONFIG = buildSchedulerConfig();
const MANAGER_INSTANCE_ID = `manager_${process.pid}_${randomUUID()}`;

function detectOcrRuntime() {
  let nvidiaDetected = false;
  let gpuName = null;
  try {
    const output = execFileSync(
      'nvidia-smi',
      ['--query-gpu=name', '--format=csv,noheader'],
      { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 4_000 },
    ).trim();
    if (output) {
      nvidiaDetected = true;
      gpuName = output.split('\n')[0].trim();
    }
  } catch (_) {}

  let cudaRuntimeAvailable = false;
  try {
    const pythonBin = path.join(ROOT_DIR, 'automation', 'venv', 'bin', 'python');
    const probe = execFileSync(
      pythonBin,
      ['-c', 'import json, torch; print(json.dumps({"available": bool(torch.cuda.is_available()), "name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))'],
      { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 8_000 },
    ).trim();
    const parsed = JSON.parse(probe);
    cudaRuntimeAvailable = parsed.available === true;
    if (cudaRuntimeAvailable) {
      nvidiaDetected = true;
      gpuName = parsed.name || gpuName;
    }
  } catch (_) {}
  return resolveOcrDevice(nvidiaDetected, cudaRuntimeAvailable, gpuName);
}

const OCR_RUNTIME = detectOcrRuntime();
console.log(`[OCR] Runtime selected: ${OCR_RUNTIME.label}${OCR_RUNTIME.gpu_name ? ` (${OCR_RUNTIME.gpu_name})` : ''}`);

const mediaStorage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, SHARED_MEDIA_DIR),
  filename: (req, file, cb) => {
    const ext = path.extname(file.originalname);
    const base = path.basename(file.originalname, ext).replace(/[^a-zA-Z0-9_-]/g, '_');
    cb(null, `${Date.now()}_${base}${ext}`);
  },
});
const uploadMedia = multer({ storage: mediaStorage });

const app = express();
const PORT = 3001;

app.use(cors());
app.use(express.json());
app.use('/shared_media', express.static(SHARED_MEDIA_DIR));

// ==========================================
// Host Resource Samplers (CPU, GPU, Docker)
// ==========================================
let prevCpuTimes = null;
let currentCpuPercent = 0;
const recentCpuSamples = [];
const cpuCores = os.cpus().length;
const cpuModel = os.cpus()[0]?.model || '';

function sampleCpu() {
  const cpus = os.cpus();
  let idle = 0, total = 0;
  for (const c of cpus) {
    for (const t in c.times) total += c.times[t];
    idle += c.times.idle;
  }
  if (prevCpuTimes) {
    const diffIdle = idle - prevCpuTimes.idle;
    const diffTotal = total - prevCpuTimes.total;
    if (diffTotal > 0) {
      currentCpuPercent = Math.min(100, Math.max(0, Math.round((1 - diffIdle / diffTotal) * 100)));
      recentCpuSamples.push(currentCpuPercent);
      if (recentCpuSamples.length > 15) recentCpuSamples.shift();
    }
  }
  prevCpuTimes = { idle, total };
}

function sustainedCpuPercent() {
  if (!recentCpuSamples.length) return currentCpuPercent;
  return recentCpuSamples.reduce((total, value) => total + value, 0) / recentCpuSamples.length;
}

let prevGpu = null;
let currentGpuPercent = 0;
let gpuModel = 'Intel Iris Xe';

// Detect GPU model
try {
  const lspci = execSync('lspci | grep -iE "vga|3d|display"', { encoding: 'utf-8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
  if (lspci) {
    const raw = lspci.split(':').pop().trim()
      .replace(/Corporation\s*/i, '')
      .replace(/\[|\]/g, '')
      .replace(/\(rev \d+\)/i, '')
      .trim();
    if (raw) gpuModel = raw;
  }
} catch (_) {}

function sampleGpu() {
  // 1. Check NVIDIA
  try {
    const nv = execSync('nvidia-smi --query-gpu=name,utilization.gpu --format=csv,noheader,nounits', {
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'ignore'],
      timeout: 300,
    }).trim();
    if (nv) {
      const parts = nv.split(',').map((s) => s.trim());
      gpuModel = parts[0] || 'NVIDIA GPU';
      currentGpuPercent = parseInt(parts[1]) || 0;
      return;
    }
  } catch (_) {}

  // 2. Check Intel DRM sysfs RC6 residency
  try {
    const rc6Paths = [
      '/sys/class/drm/card1/gt/gt0/rc6_residency_ms',
      '/sys/class/drm/card0/gt/gt0/rc6_residency_ms',
    ];
    const rc6Path = rc6Paths.find((p) => fs.existsSync(p));
    if (rc6Path) {
      const now = Date.now();
      const rc6 = parseInt(fs.readFileSync(rc6Path, 'utf-8').trim()) || 0;
      if (prevGpu && prevGpu.time) {
        const elapsed = now - prevGpu.time;
        const rc6Diff = rc6 - prevGpu.rc6;
        const active = Math.max(0, elapsed - rc6Diff);
        if (elapsed > 0) {
          currentGpuPercent = Math.min(100, Math.max(0, Math.round((active / elapsed) * 100)));
        }
      }
      prevGpu = { time: now, rc6 };
    }
  } catch (_) {}
}

let containerStatsCache = {};
let profileDiskUsageCache = {};

function sampleProfileDiskUsage() {
  try {
    if (!fs.existsSync(PROFILES_DIR)) return;
    const entries = fs.readdirSync(PROFILES_DIR, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        const profileId = entry.name;
        const profilePath = path.join(PROFILES_DIR, profileId);
        if (!fs.existsSync(path.join(profilePath, 'config.json'))) continue;
        try {
          const out = execSync(`du -sk "${profilePath}"`, {
            encoding: 'utf-8',
            stdio: ['ignore', 'pipe', 'ignore'],
            timeout: 4000,
          }).trim();
          const match = out.match(/^(\d+)/);
          if (match) {
            const kb = parseInt(match[1], 10);
            let formatted;
            if (kb >= 1024 * 1024) {
              formatted = `${(kb / (1024 * 1024)).toFixed(1)} GiB`;
            } else if (kb >= 1024) {
              formatted = `${(kb / 1024).toFixed(1)} MiB`;
            } else {
              formatted = `${kb} KiB`;
            }
            profileDiskUsageCache[profileId] = formatted;
          }
        } catch (_) {}
      }
    }
  } catch (_) {}
}

function getProfileDiskUsage(profileId) {
  if (profileDiskUsageCache[profileId]) {
    return profileDiskUsageCache[profileId];
  }
  const profilePath = path.join(PROFILES_DIR, profileId);
  if (!fs.existsSync(profilePath)) return null;
  try {
    const out = execSync(`du -sk "${profilePath}"`, {
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 3000,
    }).trim();
    const match = out.match(/^(\d+)/);
    if (match) {
      const kb = parseInt(match[1], 10);
      let formatted;
      if (kb >= 1024 * 1024) {
        formatted = `${(kb / (1024 * 1024)).toFixed(1)} GiB`;
      } else if (kb >= 1024) {
        formatted = `${(kb / 1024).toFixed(1)} MiB`;
      } else {
        formatted = `${kb} KiB`;
      }
      profileDiskUsageCache[profileId] = formatted;
      return formatted;
    }
  } catch (_) {}
  return null;
}

function sampleContainerStats() {
  try {
    const out = execSync('docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"', {
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'ignore'],
      timeout: 3000,
    }).trim();
    if (out) {
      const map = {};
      out.split('\n').forEach((line) => {
        const [name, cpu, mem] = line.split('\t');
        if (name) {
          const trimmedName = name.trim();
          let cpuStr = cpu?.trim() || '0%';
          // Profile containers have 4 cores allocated. Normalize raw docker stats (where 1 core = 100%, max 400%) to 0-100%
          if (trimmedName.startsWith('isolated_') && cpuStr.endsWith('%')) {
            const raw = parseFloat(cpuStr.replace('%', ''));
            if (!isNaN(raw)) {
              const normalized = Math.min(100, Math.round((raw / 4) * 10) / 10);
              cpuStr = `${normalized}%`;
            }
          }
          map[trimmedName] = { cpu: cpuStr, mem: mem?.trim() };
        }
      });
      containerStatsCache = map;
    }
  } catch (_) {}
}

// Initial sampling
sampleCpu();
sampleGpu();
sampleContainerStats();
sampleProfileDiskUsage();

// Periodic background samplers
setInterval(() => {
  sampleCpu();
  sampleGpu();
}, 2000);

setInterval(() => {
  sampleContainerStats();
}, 4000);

setInterval(() => {
  sampleProfileDiskUsage();
}, 10000);

// Helper: Query docker container status
function getContainerStatus(profileId) {
  try {
    const containerName = `isolated_${profileId}`;
    const output = execSync(
      `docker ps -a --filter "name=^/${containerName}$" --format "{{.Status}}"`,
      { encoding: 'utf-8' }
    ).trim();

    if (!output) return 'stopped';
    if (output.includes('Paused')) return 'paused';
    if (output.includes('Up')) return 'running';
    return 'stopped';
  } catch (err) {
    return 'stopped';
  }
}

function countRunningProfileContainers() {
  try {
    const output = execSync(
      'docker ps --filter "name=^/isolated_" --format "{{.Names}}"',
      { encoding: 'utf-8' },
    ).trim();
    return output ? output.split('\n').filter(Boolean).length : 0;
  } catch (_) {
    return 0;
  }
}

let memoryAdmissionPaused = false;
let lastQueueContainerStartAt = 0;
const CONTAINER_START_SPACING_MS = 10_000;

function resourceModeSnapshot() {
  const totalMemoryBytes = os.totalmem();
  const usedMemoryBytes = totalMemoryBytes - os.freemem();
  const memoryUsedPercent = totalMemoryBytes > 0 ? (usedMemoryBytes / totalMemoryBytes) * 100 : 0;
  const cpuPercent = sustainedCpuPercent();
  const admission = resourceAdmissionDecision({
    memoryUsedPercent,
    cpuPercent,
    pausedForMemory: memoryAdmissionPaused,
    millisecondsSinceLastStart: Date.now() - lastQueueContainerStartAt,
    startSpacingMs: CONTAINER_START_SPACING_MS,
  });
  return {
    selected_mode: resourceModeResolution.selected,
    effective_mode: resourceModeResolution.effective,
    recommended_mode: resourceModeResolution.recommended,
    limits: {
      ...SCHEDULER_CONFIG,
      ocr_threads_per_worker: recommendedOcrThreads(
        cpuCores,
        SCHEDULER_CONFIG.max_total_automation_tasks,
      ),
    },
    hardware: {
      total_memory_gb: Math.round(hostTotalMemoryGb * 10) / 10,
      cpu_threads: cpuCores,
      cpu_model: cpuModel,
      ocr: OCR_RUNTIME,
    },
    supported_modes: {
      low: resolveResourceMode('low', hostTotalMemoryGb, cpuCores).supported,
      medium: resolveResourceMode('medium', hostTotalMemoryGb, cpuCores).supported,
      high: resolveResourceMode('high', hostTotalMemoryGb, cpuCores).supported,
    },
    runtime: {
      memory_used_percent: Math.round(memoryUsedPercent * 10) / 10,
      sustained_cpu_percent: Math.round(cpuPercent * 10) / 10,
      active_profile_containers: countRunningProfileContainers(),
      admission_allowed: admission.allowed,
      admission_reason: admission.reason,
      memory_paused: memoryAdmissionPaused,
    },
  };
}

function automationWorkerEnv() {
  return {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    AUTOMATION_OCR_THREADS: String(recommendedOcrThreads(
      cpuCores,
      SCHEDULER_CONFIG.max_total_automation_tasks,
    )),
    AUTOMATION_OCR_DEVICE: OCR_RUNTIME.device,
  };
}

function claimContainerStartAdmission() {
  const totalMemoryBytes = os.totalmem();
  const memoryUsedPercent = totalMemoryBytes > 0
    ? ((totalMemoryBytes - os.freemem()) / totalMemoryBytes) * 100
    : 0;
  const decision = resourceAdmissionDecision({
    memoryUsedPercent,
    cpuPercent: sustainedCpuPercent(),
    pausedForMemory: memoryAdmissionPaused,
    millisecondsSinceLastStart: Date.now() - lastQueueContainerStartAt,
    startSpacingMs: CONTAINER_START_SPACING_MS,
  });
  memoryAdmissionPaused = decision.memoryPaused;
  if (decision.allowed) lastQueueContainerStartAt = Date.now();
  return decision;
}

function runProfileLifecycleAction(profileId, action, timeoutMs = 90_000) {
  const scriptPath = path.join(SCRIPTS_DIR, 'run_profile.sh');
  return new Promise((resolve, reject) => {
    execFile('bash', [scriptPath, profileId, action], { cwd: ROOT_DIR, timeout: timeoutMs }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error((stderr || stdout || error.message).trim()));
        return;
      }
      resolve((stdout || '').trim());
    });
  });
}

function waitMilliseconds(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function ensureProfileContainerReady(profileId) {
  const originalStatus = getContainerStatus(profileId);
  if (originalStatus === 'paused') {
    await runProfileLifecycleAction(profileId, 'unpause');
  } else if (originalStatus !== 'running') {
    await runProfileLifecycleAction(profileId, 'start');
  }

  const deadline = Date.now() + 45_000;
  const containerName = `isolated_${profileId}`;
  while (Date.now() < deadline) {
    if (getContainerStatus(profileId) !== 'running') {
      await waitMilliseconds(750);
      continue;
    }
    try {
      const windowId = execFileSync(
        'docker',
        ['exec', '-u', 'chromeuser', '-e', 'DISPLAY=:99', containerName, 'sh', '-lc', 'xdotool search --onlyvisible --class google-chrome | head -1'],
        { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 3_000 },
      ).trim();
      if (windowId) return { started: originalStatus !== 'running', status: 'running' };
    } catch (_) {}
    await waitMilliseconds(750);
  }
  throw new Error(`Container ${containerName} started but Chrome was not ready within 45 seconds`);
}

async function stopProfileContainerAfterReport(profileId) {
  if (getContainerStatus(profileId) === 'stopped') {
    return { stopped: true, already_stopped: true };
  }
  await runProfileLifecycleAction(profileId, 'stop');
  return { stopped: getContainerStatus(profileId) === 'stopped', already_stopped: false };
}

// GET /api/settings/resource-mode
app.get('/api/settings/resource-mode', (_req, res) => {
  res.json(resourceModeSnapshot());
});

// PUT /api/settings/resource-mode
app.put('/api/settings/resource-mode', (req, res) => {
  const mode = String(req.body?.mode || '').toLowerCase();
  if (!['auto', 'low', 'medium', 'high'].includes(mode)) {
    return res.status(400).json({ error: 'Resource mode must be auto, low, medium, or high.' });
  }
  const resolution = resolveResourceMode(mode, hostTotalMemoryGb, cpuCores);
  if (!resolution.supported) {
    return res.status(400).json({
      error: `${mode} mode is not supported by this machine. Recommended maximum: ${resolution.recommended}.`,
      recommended_mode: resolution.recommended,
    });
  }
  managerSettings = { resource_mode: mode, updated_at: new Date().toISOString() };
  saveManagerSettings(managerSettings);
  resourceModeResolution = resolution;
  SCHEDULER_CONFIG = buildSchedulerConfig();
  return res.json(resourceModeSnapshot());
});

// ==========================================
// Profile Endpoints
// ==========================================

// GET /api/profiles
app.get('/api/profiles', (req, res) => {
  try {
    if (!fs.existsSync(PROFILES_DIR)) {
      fs.mkdirSync(PROFILES_DIR, { recursive: true });
    }

    const entries = fs.readdirSync(PROFILES_DIR, { withFileTypes: true });
    const profiles = [];

    for (const entry of entries) {
      if (entry.isDirectory()) {
        const configPath = path.join(PROFILES_DIR, entry.name, 'config.json');
        if (fs.existsSync(configPath)) {
          try {
            const raw = fs.readFileSync(configPath, 'utf-8');
            const data = JSON.parse(raw);
            data.status = getContainerStatus(data.id);
            const containerName = `isolated_${data.id}`;
            if (containerStatsCache[containerName]) {
              data.cpu_usage = containerStatsCache[containerName].cpu;
              data.ram_usage = containerStatsCache[containerName].mem;
            }
            const diskUsage = getProfileDiskUsage(data.id);
            if (diskUsage) {
              data.disk_usage = diskUsage;
            }
            profiles.push(data);
          } catch (e) {
            console.error(`Error parsing config for ${entry.name}:`, e);
          }
        }
      }
    }

    profiles.sort((a, b) => a.id.localeCompare(b.id));
    res.json(profiles);
  } catch (err) {
    console.error('Failed to list profiles:', err);
    res.status(500).json({ error: 'Failed to list profiles' });
  }
});

// Real host hardware specifications (TigerLake Iris Xe / i7-11370H / 16GB)
const REAL_HOST_SPECS = {
  webgl_vendor: 'Intel Open Source Technology Center',
  webgl_renderer: 'Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2)',
  user_agent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  color_depth: 24,
  language: 'en-US',
  hardware_concurrency: 8,
  device_memory: 16,
};

// POST /api/profiles
app.post('/api/profiles', (req, res) => {
  try {
    const profile = req.body;
    if (!profile.id || !profile.name) {
      return res.status(400).json({ error: 'Missing profile id or name' });
    }

    const profileDir = path.join(PROFILES_DIR, profile.id);
    const dataDir = path.join(profileDir, 'chrome_data');
    const configPath = path.join(profileDir, 'config.json');

    fs.mkdirSync(dataDir, { recursive: true });

    // Assign proxy from pool if auto-assign requested, or sync if host provided
    profile.network = profile.network || { proxy_type: 'socks5', proxy_host: '', proxy_port: 1080 };
    let assignedProxy = null;
    if (profile.network.auto_assign || (!profile.network.proxy_host && profile.network.auto_assign !== false && !profile.network.direct)) {
      assignedProxy = assignProxy(profile.id);
      if (assignedProxy) {
        profile.network.proxy_host = assignedProxy.host;
        profile.network.proxy_port = assignedProxy.port;
        profile.network.proxy_user = assignedProxy.username || '';
        profile.network.proxy_pass = assignedProxy.password || '';
        console.log(`Auto-assigned proxy ${assignedProxy.host}:${assignedProxy.port} to ${profile.id}`);
      }
    } else if (profile.network.proxy_host) {
      assignedProxy = syncProxyAssignment(profile.id, profile.network.proxy_host, profile.network.proxy_port);
    }

    // Ensure authentic host real device specs while preserving user-selected resolution & proxy timezone
    const selectedResolution = profile.fingerprint?.screen_resolution || '1920x1080';
    const selectedTimezone = (assignedProxy && assignedProxy.timezone) || profile.fingerprint?.timezone || 'America/Los_Angeles';

    profile.fingerprint = {
      ...REAL_HOST_SPECS,
      ...profile.fingerprint,
      webgl_vendor: REAL_HOST_SPECS.webgl_vendor,
      webgl_renderer: REAL_HOST_SPECS.webgl_renderer,
      hardware_concurrency: REAL_HOST_SPECS.hardware_concurrency,
      device_memory: REAL_HOST_SPECS.device_memory,
      user_agent: REAL_HOST_SPECS.user_agent,
      color_depth: REAL_HOST_SPECS.color_depth,
      screen_resolution: selectedResolution,
      timezone: selectedTimezone,
    };

    profile.created_at = new Date().toISOString();
    profile.status = 'stopped';
    profile.container = profile.container || {};
    profile.container.volume_path = `profiles/${profile.id}/chrome_data`;

    fs.writeFileSync(configPath, JSON.stringify(profile, null, 2), 'utf-8');
    res.status(201).json(profile);
  } catch (err) {
    console.error('Failed to create profile:', err);
    res.status(500).json({ error: 'Failed to create profile' });
  }
});

// PUT /api/profiles/:id
app.put('/api/profiles/:id', (req, res) => {
  try {
    const { id } = req.params;
    const updates = req.body;
    const configPath = path.join(PROFILES_DIR, id, 'config.json');

    if (!fs.existsSync(configPath)) {
      return res.status(404).json({ error: 'Profile not found' });
    }

    const existing = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

    const updated = {
      ...existing,
      name: updates.name !== undefined ? updates.name : existing.name,
      fingerprint: { ...existing.fingerprint, ...updates.fingerprint },
      network: { ...existing.network, ...updates.network },
      account: { ...existing.account, ...updates.account },
    };

    if (updates.network) {
      const bound = syncProxyAssignment(id, updated.network.proxy_host, updated.network.proxy_port);
      if (bound && bound.timezone) {
        updated.fingerprint.timezone = bound.timezone;
      }
    }

    fs.writeFileSync(configPath, JSON.stringify(updated, null, 2), 'utf-8');
    res.json(updated);
  } catch (err) {
    console.error('Failed to update profile:', err);
    res.status(500).json({ error: 'Failed to update profile' });
  }
});

// POST /api/profiles/:id/action
app.post('/api/profiles/:id/action', (req, res) => {
  const { id } = req.params;
  const { action } = req.body;

  if (!['start', 'stop', 'pause', 'unpause'].includes(action)) {
    return res.status(400).json({ error: 'Invalid action' });
  }

  const scriptPath = path.join(SCRIPTS_DIR, 'run_profile.sh');
  const cmd = `bash "${scriptPath}" "${id}" "${action}"`;

  exec(cmd, { cwd: ROOT_DIR }, (error, stdout, stderr) => {
    if (error) {
      console.error(`Error running ${action} on ${id}:`, stderr);
      return res.status(500).json({ error: stderr || error.message });
    }
    res.json({ success: true, message: stdout.trim() });
  });
});

// POST /api/profiles/:id/paste
app.post('/api/profiles/:id/paste', (req, res) => {
  try {
    const { id } = req.params;
    const { text, mode = 'both' } = req.body; // 'both' | 'type' | 'clipboard'

    if (text === undefined || text === null) {
      return res.status(400).json({ error: 'Text is required' });
    }

    const containerName = `isolated_${id}`;

    // Verify container is running
    const ps = execSync(`docker ps --filter "name=^/${containerName}$" -q`, { encoding: 'utf-8' }).trim();
    if (!ps) {
      return res.status(400).json({ error: `Container ${containerName} is not running` });
    }

    // Base64 encode to safely transmit any unicode, emojis, newlines, or quotes
    const b64 = Buffer.from(text).toString('base64');

    const pythonScript = `
import base64, subprocess, sys

raw_b64 = "${b64}"
text = base64.b64decode(raw_b64).decode("utf-8")
mode = "${mode}"

# 1. Update X11 CLIPBOARD and PRIMARY
try:
    p1 = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
    p1.communicate(text.encode("utf-8"))
    p2 = subprocess.Popen(["xclip", "-selection", "primary"], stdin=subprocess.PIPE)
    p2.communicate(text.encode("utf-8"))
except Exception as e:
    pass

# 2. Handle injection mode
if mode == "type":
    try:
        win_res = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "google-chrome"], stdout=subprocess.PIPE, text=True)
        win_ids = [w.strip() for w in win_res.stdout.split() if w.strip()]
        if win_ids:
            subprocess.run(["xdotool", "windowfocus", win_ids[-1]], check=False)
            subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "8", text], check=False)
    except Exception as e:
        pass
elif mode == "paste" or mode == "both":
    try:
        win_res = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "google-chrome"], stdout=subprocess.PIPE, text=True)
        win_ids = [w.strip() for w in win_res.stdout.split() if w.strip()]
        if win_ids:
            subprocess.run(["xdotool", "windowfocus", win_ids[-1]], check=False)
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=False)
    except Exception as e:
        pass
`;

    execSync(
      `docker exec -i -u chromeuser -e DISPLAY=:99 ${containerName} python3 -c '${pythonScript.replace(/'/g, "'\"'\"'")}'`,
      { timeout: 15000 }
    );

    res.json({ success: true, message: `Text transferred to ${id}` });
  } catch (err) {
    console.error('Error pasting to container:', err);
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/profiles/:id
app.delete('/api/profiles/:id', (req, res) => {
  const { id } = req.params;
  const deleteData = req.query.deleteData === 'true';

  try {
    // Release assigned proxy back to pool
    releaseProxy(id);

    // Stop and rm container
    try {
      execSync(`docker rm -f isolated_${id}`, { stdio: 'ignore' });
    } catch (_) {}

    const profileDir = path.join(PROFILES_DIR, id);
    if (fs.existsSync(profileDir)) {
      if (deleteData) {
        fs.rmSync(profileDir, { recursive: true, force: true });
      } else {
        const configPath = path.join(profileDir, 'config.json');
        if (fs.existsSync(configPath)) {
          fs.unlinkSync(configPath);
        }
      }
    }

    delete profileDiskUsageCache[id];
    res.json({ success: true });
  } catch (err) {
    console.error(`Failed to delete profile ${id}:`, err);
    res.status(500).json({ error: err.message });
  }
});

// POST /api/profiles/:id/clean-evidence
app.post('/api/profiles/:id/clean-evidence', (req, res) => {
  const { id } = req.params;
  const days = Number(req.body?.days) > 0 ? Number(req.body.days) : 3;
  const profileDir = path.join(PROFILES_DIR, id);

  if (!fs.existsSync(profileDir)) {
    return res.status(404).json({ error: `Profile ${id} not found` });
  }

  try {
    const result = cleanProfileEvidence(profileDir, days);
    delete profileDiskUsageCache[id];
    const newDiskUsage = getProfileDiskUsage(id);

    res.json({
      success: true,
      profile_id: id,
      days_kept: days,
      ...result,
      new_disk_usage: newDiskUsage,
    });
  } catch (err) {
    console.error(`Failed to clean evidence for ${id}:`, err);
    res.status(500).json({ error: err.message || 'Failed to clean evidence' });
  }
});

// POST /api/evidence/clean
app.post('/api/evidence/clean', (req, res) => {
  const days = Number(req.body?.days) > 0 ? Number(req.body.days) : 3;

  try {
    const result = cleanAllProfilesEvidence(PROFILES_DIR, days);
    profileDiskUsageCache = {};
    sampleProfileDiskUsage();

    res.json({
      success: true,
      days_kept: days,
      ...result,
    });
  } catch (err) {
    console.error('Failed to clean evidence across all profiles:', err);
    res.status(500).json({ error: err.message || 'Failed to clean evidence' });
  }
});

// ==========================================
// Proxy Management Endpoints
// ==========================================

// GET /api/proxies
app.get('/api/proxies', (req, res) => {
  try {
    const pool = loadProxyPool();
    res.json(pool);
  } catch (err) {
    res.status(500).json({ error: 'Failed to load proxy pool' });
  }
});

// POST /api/proxies/import
app.post('/api/proxies/import', (req, res) => {
  try {
    const { text } = req.body;
    if (!text || typeof text !== 'string') {
      return res.status(400).json({ error: 'No proxy text provided' });
    }

    const result = importProxiesFromText(text);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: 'Failed to import proxies' });
  }
});

// POST /api/proxies/:id/test
app.post('/api/proxies/:id/test', async (req, res) => {
  try {
    const { id } = req.params;
    const pool = loadProxyPool();
    const proxy = pool.find((p) => p.id === id);

    if (!proxy) {
      return res.status(404).json({ error: 'Proxy not found' });
    }

    const testRes = await testProxyPing(proxy.host, proxy.port);
    proxy.latency_ms = testRes.latency_ms;
    proxy.last_checked = new Date().toISOString();
    saveProxyPool(pool);

    res.json({ ...proxy, test: testRes });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/proxies/:id
app.delete('/api/proxies/:id', (req, res) => {
  try {
    const { id } = req.params;
    deleteProxy(id);
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ==========================================
// Automation Task Endpoints (Zero-CDP)
// ==========================================

const activeAutomationTasks = new Map();

function serializeTaskState(task) {
  if (!task) return { status: 'idle', logs: [] };
  const { process, ...clientData } = task;
  return clientData;
}

function normalizeStoredLog(line, profileId) {
  const value = String(line || '').trim();
  const match = value.match(/^\[(.*?)\]\s*\[(.*?)\]\s*\[(.*?)\]\s*(.*)$/);
  if (match) {
    return { timestamp: match[1], profile_id: match[2], level: match[3], message: match[4] };
  }
  const isStderr = value.startsWith('[STDERR]');
  return {
    timestamp: '',
    profile_id: profileId,
    level: isStderr ? 'WARN' : 'INFO',
    message: isStderr ? value.slice(8).trim() : value,
  };
}

function latestQueueTaskState(profileId) {
  const queue = loadPostingQueue();
  const candidates = [];
  for (const batch of queue.daily_batches || []) {
    for (const post of batch.posts || []) {
      for (const execution of post.executions || []) {
        if (execution.profile_id === profileId) {
          candidates.push({
            execution,
            task: post.type === 'warming' ? 'warming' : (post.type === 'reel' ? 'reel' : 'post'),
          });
        }
      }
    }
  }
  if (!candidates.length) return null;
  candidates.sort((a, b) => {
    const aTime = Date.parse(a.execution.started_at || a.execution.published_at || a.execution.scheduled_at || 0) || 0;
    const bTime = Date.parse(b.execution.started_at || b.execution.published_at || b.execution.scheduled_at || 0) || 0;
    return bTime - aTime;
  });
  const { execution, task } = candidates[0];
  const active = activeAutomationTasks.get(profileId);
  const isProcessActive = active && active.status === 'running' && active.process && !active.process.killed && active.process.exitCode === null;

  let rawStatus = execution.status;
  if (rawStatus === 'running' && !isProcessActive) {
    rawStatus = 'stopped';
  }

  const statusMap = {
    pending: 'idle',
    ready: 'idle',
    preparing: 'running',
    running: 'running',
    published: 'completed',
    completed: 'completed',
    failed: 'failed',
    failed_before_publish: 'failed',
    uncertain: 'uncertain',
    needs_review: 'uncertain',
    stopped: 'stopped',
  };
  return {
    profile_id: profileId,
    task,
    status: statusMap[rawStatus] || 'idle',
    started_at: execution.started_at || execution.scheduled_at,
    ended_at: execution.ended_at || execution.published_at || null,
    logs: (execution.logs || []).slice(-300).map((line) => normalizeStoredLog(line, profileId)),
    error: execution.error || null,
    evidence_dir: execution.evidence_dir || null,
    execution_id: execution.execution_id,
    source: 'queue',
  };
}

// POST /api/automation/run
app.post('/api/automation/run', (req, res) => {
  try {
    const { profile_id, task, scrolls, caption, comment_link, media } = req.body;

    if (!profile_id || !task) {
      return res.status(400).json({ error: 'profile_id and task are required' });
    }

    if (task !== 'warming' && task !== 'post' && task !== 'reel' && task !== 'comment') {
      return res.status(400).json({ error: `Invalid task "${task}". Allowed: warming, post, reel, comment` });
    }

    const existing = activeAutomationTasks.get(profile_id);
    if (existing && existing.status === 'running') {
      return res.status(400).json({ error: `Automation is already running for profile "${profile_id}"` });
    }

    const profileDir = path.join(PROFILES_DIR, profile_id);
    if (!fs.existsSync(profileDir)) {
      return res.status(404).json({ error: `Profile "${profile_id}" not found` });
    }

    const slotKind = task === 'warming' ? 'preparer' : 'publisher';
    const slotCheck = canAcquireSchedulerSlot(
      loadPostingQueue(),
      activeInMemorySchedulerLeases(),
      slotKind,
      profile_id,
      SCHEDULER_CONFIG,
    );
    if (!slotCheck.allowed) {
      return res.status(409).json({
        error: `Scheduler capacity unavailable: ${slotCheck.reason}`,
        scheduler: slotCheck.snapshot,
      });
    }
    const manualLeaseNow = Date.now();
    const manualLease = {
      lease_id: randomUUID(),
      owner_id: MANAGER_INSTANCE_ID,
      kind: slotKind,
      profile_id,
      claimed_at: new Date(manualLeaseNow).toISOString(),
      heartbeat_at: new Date(manualLeaseNow).toISOString(),
      expires_at: new Date(manualLeaseNow + SCHEDULER_CONFIG.lease_ttl_ms).toISOString(),
    };

    const pythonBin = path.join(ROOT_DIR, 'automation', 'venv', 'bin', 'python');
    const runnerScript = path.join(ROOT_DIR, 'automation', 'runner.py');

    if (!fs.existsSync(pythonBin)) {
      return res.status(500).json({ error: 'Automation virtualenv python not found at ' + pythonBin });
    }

    const args = ['-u', runnerScript, '--profile', profile_id, '--task', task];
    if (scrolls !== undefined && scrolls !== null) {
      args.push('--scrolls', String(scrolls));
    }
    if (caption) {
      args.push('--caption', caption);
    }
    if (comment_link) {
      args.push('--comment-link', comment_link);
    }
    if (media) {
      args.push('--media', media);
    }

    const taskRecord = {
      profile_id,
      task,
      status: 'running',
      started_at: new Date().toISOString(),
      ended_at: null,
      logs: [],
      result: null,
      error: null,
      process: null,
      scheduler_lease: manualLease,
      last_activity_at: new Date().toISOString(),
    };

    const proc = spawn(pythonBin, args, {
      cwd: ROOT_DIR,
      env: automationWorkerEnv(),
    });

    taskRecord.process = proc;
    activeAutomationTasks.set(profile_id, taskRecord);

    const appendLog = (line, defaultLevel = 'INFO') => {
      const trimmed = line.trim();
      if (!trimmed) return;

      if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed && typeof parsed === 'object' && ('success' in parsed || 'profile_id' in parsed)) {
            taskRecord.result = parsed;
            return;
          }
        } catch (_) {}
      }

      const match = trimmed.match(/^\[(.*?)\]\s*\[(.*?)\]\s*\[(.*?)\]\s*(.*)$/);
      if (match) {
        taskRecord.logs.push({
          timestamp: match[1],
          profile_id: match[2],
          level: match[3],
          message: match[4],
        });
      } else {
        taskRecord.logs.push({
          timestamp: new Date().toISOString(),
          profile_id,
          level: defaultLevel,
          message: trimmed,
        });
      }

      if (taskRecord.logs.length > 300) {
        taskRecord.logs.splice(0, taskRecord.logs.length - 300);
      }
    };

    let stdoutBuffer = '';
    proc.stdout.on('data', (data) => {
      taskRecord.last_activity_at = new Date().toISOString();
      stdoutBuffer += data.toString();
      const lines = stdoutBuffer.split('\n');
      stdoutBuffer = lines.pop();
      for (const line of lines) {
        appendLog(line, 'INFO');
      }
    });

    let stderrBuffer = '';
    proc.stderr.on('data', (data) => {
      taskRecord.last_activity_at = new Date().toISOString();
      stderrBuffer += data.toString();
      const lines = stderrBuffer.split('\n');
      stderrBuffer = lines.pop();
      for (const line of lines) {
        appendLog(line, 'WARN');
      }
    });

    proc.on('close', (code, signal) => {
      if (stdoutBuffer.trim()) appendLog(stdoutBuffer.trim(), 'INFO');
      if (stderrBuffer.trim()) appendLog(stderrBuffer.trim(), 'WARN');

      taskRecord.ended_at = new Date().toISOString();
      taskRecord.process = null;
      taskRecord.last_scheduler_lease = taskRecord.scheduler_lease;
      taskRecord.scheduler_lease = null;

      if (taskRecord.status === 'stopped') {
        appendLog(`[${taskRecord.ended_at}] [${profile_id}] [INFO] Task stopped by user.`, 'INFO');
      } else if (taskRecord.result?.status?.startsWith('skipped_')) {
        taskRecord.status = taskRecord.result.status;
        taskRecord.error = taskRecord.result.error || null;
        appendLog(`[${taskRecord.ended_at}] [${profile_id}] [INFO] Profile safely skipped: ${taskRecord.status}.`, 'INFO');
      } else if (taskRecord.result?.status === 'needs_review' || taskRecord.result?.status === 'uncertain' || code === 2) {
        taskRecord.status = taskRecord.result?.status === 'needs_review' ? 'needs_review' : 'uncertain';
        taskRecord.error = taskRecord.result?.error || 'Publication requires operator review or could not be visually confirmed';
        appendLog(`[${taskRecord.ended_at}] [${profile_id}] [WARN] Task outcome is ${taskRecord.status} and requires review.`, 'WARN');
      } else if (code === 0) {
        taskRecord.status = 'completed';
        appendLog(`[${taskRecord.ended_at}] [${profile_id}] [SUCCESS] Task completed successfully.`, 'SUCCESS');
      } else {
        taskRecord.status = 'failed';
        taskRecord.error = `Process exited with code ${code}${signal ? ` (signal ${signal})` : ''}`;
        appendLog(`[${taskRecord.ended_at}] [${profile_id}] [ERROR] Task failed: ${taskRecord.error}`, 'ERROR');
      }
    });

    proc.on('error', (err) => {
      taskRecord.status = 'failed';
      taskRecord.error = err.message;
      taskRecord.ended_at = new Date().toISOString();
      taskRecord.process = null;
      taskRecord.last_scheduler_lease = taskRecord.scheduler_lease;
      taskRecord.scheduler_lease = null;
      appendLog(`[${taskRecord.ended_at}] [${profile_id}] [ERROR] Failed to start task: ${err.message}`, 'ERROR');
    });

    res.json({
      success: true,
      message: `Started "${task}" task for profile "${profile_id}"`,
      state: serializeTaskState(taskRecord),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/automation/status/:profile_id
app.get('/api/automation/status/:profile_id', (req, res) => {
  try {
    const { profile_id } = req.params;
    const taskRecord = activeAutomationTasks.get(profile_id);
    if (!taskRecord) {
      return res.json(latestQueueTaskState(profile_id) || { profile_id, status: 'idle', logs: [] });
    }
    res.json(serializeTaskState(taskRecord));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/automation/stop/:profile_id
app.post('/api/automation/stop/:profile_id', (req, res) => {
  try {
    const { profile_id } = req.params;
    const taskRecord = activeAutomationTasks.get(profile_id);
    if (!taskRecord || taskRecord.status !== 'running') {
      return res.status(400).json({ error: `No active automation running for profile "${profile_id}"` });
    }

    taskRecord.status = 'stopped';
    if (taskRecord.process) {
      taskRecord.process.kill('SIGTERM');
      setTimeout(() => {
        try {
          if (taskRecord.process) taskRecord.process.kill('SIGKILL');
        } catch (_) {}
      }, 2000);
    }

    res.json({ success: true, message: `Terminated automation for profile "${profile_id}"` });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/automation/tasks
app.get('/api/automation/tasks', (req, res) => {
  try {
    const tasks = {};
    for (const [id, record] of activeAutomationTasks.entries()) {
      tasks[id] = serializeTaskState(record);
    }
    res.json(tasks);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ==========================================
// Phase 2: Batch Posting Queue & Media Storage
// ==========================================

function loadPostingQueue() {
  try {
    if (fs.existsSync(QUEUE_FILE)) {
      const raw = fs.readFileSync(QUEUE_FILE, 'utf-8');
      return JSON.parse(raw);
    }
  } catch (err) {
    console.error('Error loading posting_queue.json:', err);
  }
  return { queue_version: '2.0', daily_batches: [] };
}

function savePostingQueue(data) {
  try {
    const tmpFile = `${QUEUE_FILE}.tmp`;
    fs.writeFileSync(tmpFile, JSON.stringify(data, null, 2), 'utf-8');
    fs.renameSync(tmpFile, QUEUE_FILE);
    return true;
  } catch (err) {
    console.error('Error atomically saving posting_queue.json:', err);
    return false;
  }
}

function withQueueClaimLock(callback) {
  let descriptor = null;
  try {
    try {
      descriptor = fs.openSync(QUEUE_CLAIM_LOCK_FILE, 'wx');
    } catch (err) {
      if (err.code !== 'EEXIST') throw err;
      const stat = fs.statSync(QUEUE_CLAIM_LOCK_FILE);
      if (Date.now() - stat.mtimeMs <= SCHEDULER_CONFIG.lease_ttl_ms) {
        throw new Error('queue_claim_in_progress');
      }
      fs.unlinkSync(QUEUE_CLAIM_LOCK_FILE);
      descriptor = fs.openSync(QUEUE_CLAIM_LOCK_FILE, 'wx');
    }
    fs.writeFileSync(descriptor, JSON.stringify({ owner_id: MANAGER_INSTANCE_ID, pid: process.pid }));
    return callback();
  } finally {
    if (descriptor !== null) {
      try { fs.closeSync(descriptor); } catch (_) {}
      try { fs.unlinkSync(QUEUE_CLAIM_LOCK_FILE); } catch (_) {}
    }
  }
}

function isTaskProcessActive(record) {
  return Boolean(
    record?.status === 'running'
    && (
      record.lifecycle_active === true
      || (record.process && !record.process.killed && record.process.exitCode === null)
    )
  );
}

function activeInMemorySchedulerLeases() {
  const leases = [];
  for (const record of activeAutomationTasks.values()) {
    if (isTaskProcessActive(record) && isLeaseActive(record.scheduler_lease)) {
      leases.push(record.scheduler_lease);
    }
  }
  return leases;
}

function currentSchedulerSnapshot(queue = loadPostingQueue()) {
  return schedulerSnapshot(queue, activeInMemorySchedulerLeases(), SCHEDULER_CONFIG);
}

function findQueueExecution(queue, executionId) {
  for (const batch of queue.daily_batches || []) {
    for (const post of batch.posts || []) {
      const execution = (post.executions || []).find((item) => item.execution_id === executionId);
      if (execution) return { execution, post, batch };
    }
  }
  return null;
}

function persistQueueExecutionStage(executionId, stage, timestamp = new Date().toISOString(), leaseId = null) {
  if (!executionId || !stage) return false;
  const queue = loadPostingQueue();
  const match = findQueueExecution(queue, executionId);
  if (!match) return false;

  const { execution } = match;
  if (leaseId && execution.scheduler_lease?.lease_id !== leaseId) return false;
  const history = Array.isArray(execution.stage_history) ? execution.stage_history : [];
  const previous = history[history.length - 1];
  if (!previous || previous.stage !== stage) {
    history.push({ stage, timestamp });
  }
  execution.stage = stage;
  execution.stage_history = history;
  execution.stage_updated_at = timestamp;
  return savePostingQueue(queue);
}

function applyInterruptedExecutionRecovery(execution, now = new Date().toISOString()) {
  const recovery = classifyInterruptedExecution(execution);
  execution.last_active_stage = recovery.interruptedStage;
  execution.status = recovery.status;
  execution.stage = recovery.stage;
  execution.ended_at = now;
  execution.recovered_at = now;
  execution.error = recovery.error;
  execution.stage_history = Array.isArray(execution.stage_history) ? execution.stage_history : [];
  execution.stage_history.push({
    stage: recovery.stage,
    timestamp: now,
    reason: 'manager_restart_recovery',
    interrupted_stage: recovery.interruptedStage,
  });
}

function recoverStaleQueueExecutions(reason = 'expired_scheduler_lease') {
  try {
    const queue = loadPostingQueue();
    let modified = false;
    const liveLeaseIds = new Set(activeInMemorySchedulerLeases().map((lease) => lease.lease_id));
    const liveCommentRetryIds = new Set(
      [...activeAutomationTasks.values()]
        .filter((record) => isTaskProcessActive(record) && record.comment_retry === true)
        .map((record) => record.queue_execution_id),
    );
    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        for (const execution of post.executions || []) {
          if (
            execution.comment_retry_status === 'running'
            && !liveCommentRetryIds.has(execution.execution_id)
          ) {
            execution.comment_retry_status = 'interrupted';
            execution.comment_retry_ended_at = new Date().toISOString();
            execution.first_comment_status = 'submission_pending';
            execution.first_comment_note = 'Comment-only retry was interrupted; no automatic retry was attempted.';
            modified = true;
          }
        }
      }
    }
    for (const execItem of findStaleRunningExecutions(queue, liveLeaseIds)) {
      const staleLease = execItem.scheduler_lease || null;
      applyInterruptedExecutionRecovery(execItem);
      execItem.scheduler_recovery_reason = reason;
      execItem.last_scheduler_lease = staleLease;
      execItem.scheduler_lease = null;
      console.log(
        `[Scheduler] Recovered stale execution ${execItem.execution_id} as ${execItem.status} `
        + `(last active stage: ${execItem.last_active_stage}).`
      );
      modified = true;
    }
    if (modified) {
      savePostingQueue(queue);
    }
  } catch (err) {
    console.error('Error recovering stale queue leases:', err);
  }
}

recoverStaleQueueExecutions('manager_startup_stale_lease');

function heartbeatSchedulerLeases() {
  const nowMs = Date.now();
  const queue = loadPostingQueue();
  let queueModified = false;
  for (const record of activeAutomationTasks.values()) {
    if (!isTaskProcessActive(record) || !record.scheduler_lease?.lease_id) continue;
    const lastActivityMs = Date.parse(record.last_activity_at || record.started_at || 0) || 0;
    const stage = record.current_stage || record.result?.current_stage || 'unknown';
    const silenceTimeoutMs = workerSilenceTimeoutMs(stage, SCHEDULER_CONFIG.lease_ttl_ms);
    if (nowMs - lastActivityMs >= silenceTimeoutMs) {
      record.status = 'stale';
      record.error = `Live worker exceeded the ${Math.round(silenceTimeoutMs / 60000)}-minute silence limit in stage '${stage}'`;
      try { record.process?.kill('SIGTERM'); } catch (_) {}
      setTimeout(() => {
        try {
          if (record.process && record.process.exitCode === null) record.process.kill('SIGKILL');
        } catch (_) {}
      }, 2_000).unref?.();
      continue;
    }
    const leaseId = record.scheduler_lease.lease_id;
    if (record.queue_execution_id) {
      const match = findQueueExecution(queue, record.queue_execution_id);
      if (match && refreshExecutionLease(match.execution, leaseId, SCHEDULER_CONFIG, nowMs)) {
        record.scheduler_lease = { ...match.execution.scheduler_lease };
        queueModified = true;
      }
    } else if (refreshExecutionLease(record, leaseId, SCHEDULER_CONFIG, nowMs)) {
      record.scheduler_lease = { ...record.scheduler_lease };
    }
  }
  if (queueModified) savePostingQueue(queue);
  recoverStaleQueueExecutions('scheduler_lease_expired');
}

const schedulerHeartbeatTimer = setInterval(
  heartbeatSchedulerLeases,
  SCHEDULER_CONFIG.heartbeat_interval_ms,
);
schedulerHeartbeatTimer.unref?.();

// AI Caption Spinner Helper
function generateSpunCaption(baseCaption, index = 0, profileId = '') {
  if (!baseCaption || !baseCaption.trim()) return '';
  const hooks = [
    "Here's something you need to know 👇",
    "Take a look at this ⚡",
    "If you haven't seen this yet, watch closely 👀",
    "Quick tip for today 🚀",
    "Game changer: don't sleep on this 💡",
    "Consistency beats talent every single day 🔥",
    "Check this out right now 💥",
    "Save this for your routine 📌",
    "Must watch: here is how it works 🎯",
    "Real growth starts with small steps daily ✨"
  ];
  const closers = [
    "\n\nDrop a comment if you agree! 💬",
    "\n\nSave this for later 📌",
    "\n\nTag someone who needs to see this! 👇",
    "\n\nFollow for more daily insights 🚀",
    "\n\nWhat are your thoughts on this? 💭",
    "\n\nHit save to try this out! ⚡"
  ];
  const seed = (index + profileId.length + baseCaption.length) % hooks.length;
  const hook = hooks[seed];
  const closer = closers[(seed + 2) % closers.length];
  return `${hook}\n\n${baseCaption.trim()}${closer}`;
}

// Media Upload Endpoint
// POST /api/media/upload
app.post('/api/media/upload', uploadMedia.array('files', 20), (req, res) => {
  try {
    if (!req.files || req.files.length === 0) {
      return res.status(400).json({ error: 'No files uploaded' });
    }
    const uploaded = req.files.map((file) => {
      const ext = path.extname(file.filename).toLowerCase();
      const isVideo = ['.mp4', '.mov', '.webm'].includes(ext);
      return {
        filename: file.filename,
        original_name: file.originalname,
        size_bytes: file.size,
        type: isVideo ? 'reel' : 'photo',
        url: `/shared_media/${file.filename}`,
        path: path.join(SHARED_MEDIA_DIR, file.filename),
      };
    });
    res.json({ success: true, count: uploaded.length, files: uploaded });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/media/list
app.get('/api/media/list', (req, res) => {
  try {
    if (!fs.existsSync(SHARED_MEDIA_DIR)) {
      return res.json([]);
    }
    const files = fs.readdirSync(SHARED_MEDIA_DIR);
    const result = files
      .filter((f) => !f.startsWith('.'))
      .map((filename) => {
        const fullPath = path.join(SHARED_MEDIA_DIR, filename);
        const stat = fs.statSync(fullPath);
        const ext = path.extname(filename).toLowerCase();
        const isVideo = ['.mp4', '.mov', '.webm'].includes(ext);
        return {
          filename,
          size_bytes: stat.size,
          created_at: stat.birthtime,
          type: isVideo ? 'reel' : 'photo',
        };
      })
      .sort((a, b) => b.created_at - a.created_at);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/ai/spin-caption
app.post('/api/ai/spin-caption', (req, res) => {
  try {
    const { base_caption, count = 3, profile_ids = [] } = req.body;
    if (!base_caption) {
      return res.status(400).json({ error: 'base_caption is required' });
    }
    const variations = [];
    const num = Math.max(1, parseInt(count, 10) || 3);
    for (let i = 0; i < num; i++) {
      const pid = profile_ids[i] || `profile_${i + 1}`;
      variations.push({
        profile_id: pid,
        spun_caption: generateSpunCaption(base_caption, i, pid),
      });
    }
    res.json({ success: true, variations });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/queue
app.get('/api/queue', (req, res) => {
  try {
    const queue = loadPostingQueue();
    // Gather flat list of all executions for easy frontend consumption
    const allExecutions = [];
    (queue.daily_batches || []).forEach((batch) => {
      (batch.posts || []).forEach((post) => {
        (post.executions || []).forEach((execItem) => {
          allExecutions.push({
            ...execItem,
            batch_id: batch.batch_id,
            batch_name: batch.name,
            post_type: post.type,
            media_file: post.media_file,
            base_caption: post.base_caption,
            first_comment: post.first_comment,
            scrolls: post.scrolls,
          });
        });
      });
    });

    // Sort executions by scheduled_at ascending
    allExecutions.sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));

    const stats = {
      total: allExecutions.length,
      pending: allExecutions.filter((e) => ['pending', 'ready'].includes(e.status)).length,
      running: allExecutions.filter((e) => ['running', 'preparing'].includes(e.status)).length,
      published: allExecutions.filter((e) => e.status === 'published').length,
      completed: allExecutions.filter((e) => e.status === 'completed').length,
      failed: allExecutions.filter((e) => e.status === 'failed' || e.status === 'failed_before_publish').length,
      uncertain: allExecutions.filter((e) => e.status === 'uncertain' || e.status === 'needs_review').length,
      skipped: allExecutions.filter((e) => e.status && e.status.startsWith('skipped')).length,
    };

    res.json({
      queue_version: queue.queue_version,
      stats,
      scheduler: currentSchedulerSnapshot(queue),
      telemetry_summary: buildQueueTelemetrySummary(allExecutions),
      batches: queue.daily_batches || [],
      executions: allExecutions,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/queue/batch
app.post('/api/queue/batch', (req, res) => {
  try {
    const { name, target_profiles, schedule_window, posts } = req.body;

    if (!Array.isArray(target_profiles) || target_profiles.length === 0) {
      return res.status(400).json({ error: 'Please select at least one target profile.' });
    }
    if (!Array.isArray(posts) || posts.length === 0) {
      return res.status(400).json({ error: 'Please provide at least one post item.' });
    }

    const queue = loadPostingQueue();
    const batchId = `batch_${Date.now()}`;
    const startTimeStr = schedule_window?.start_time || '09:00';
    const endTimeStr = schedule_window?.end_time || '21:00';
    const rawStaggerSeconds = parseInt(schedule_window?.profile_stagger_seconds, 10);
    const legacyStaggerMinutes = parseInt(schedule_window?.profile_stagger_minutes, 10);
    const staggerSeconds = !Number.isNaN(rawStaggerSeconds)
      ? Math.max(0, rawStaggerSeconds)
      : !Number.isNaN(legacyStaggerMinutes)
        ? Math.max(0, legacyStaggerMinutes) * 60
        : 60;
    const preparationMode = schedule_window?.session_preparation_mode || 'off';
    if (!['off', 'brief', 'extended'].includes(preparationMode)) {
      return res.status(400).json({ error: "session_preparation_mode must be 'off', 'brief', or 'extended'" });
    }

    // Filter target profiles by profile configuration killswitch
    const acceptedProfiles = [];
    const skippedProfiles = [];

    for (const pid of target_profiles) {
      const cfgPath = path.join(PROFILES_DIR, pid, 'config.json');
      if (fs.existsSync(cfgPath)) {
        try {
          const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf-8'));
          if (cfg.automation && cfg.automation.enabled === false) {
            skippedProfiles.push({ profile_id: pid, reason: cfg.automation.reason || 'manual_control_only' });
            continue;
          }
        } catch (_) {}
      }
      acceptedProfiles.push(pid);
    }

    if (acceptedProfiles.length === 0) {
      return res.status(400).json({
        error: 'All selected profiles have automation disabled in their config.json.',
        skipped: skippedProfiles,
      });
    }

    const previousBatch = (queue.daily_batches || [])[0];
    const previousOrder = previousBatch?.profile_execution_order || previousBatch?.target_profiles || [];
    const previousLastProfile = previousOrder[previousOrder.length - 1] || null;
    const profileExecutionOrder = shuffledProfileOrder(acceptedProfiles, previousLastProfile);

    // Generate posting order shuffle per profile
    const postingOrders = {};
    for (const pid of acceptedProfiles) {
      const order = [...Array(posts.length).keys()];
      for (let i = order.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [order[i], order[j]] = [order[j], order[i]];
      }
      postingOrders[pid] = order;
    }

    const isStartNow = req.body.start_now === true || schedule_window?.start_now === true;
    const now = new Date();
    const [startH, startM] = startTimeStr.split(':').map((v) => parseInt(v, 10) || 0);
    const [endH, endM] = endTimeStr.split(':').map((v) => parseInt(v, 10) || 0);

    const windowStart = isStartNow
      ? new Date(now)
      : new Date(now.getFullYear(), now.getMonth(), now.getDate(), startH, startM, 0);

    const windowEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), endH, endM, 0);
    if (windowEnd <= windowStart) {
      windowEnd.setDate(windowEnd.getDate() + 1);
    }

    const totalWindowMs = Math.max(10 * 60 * 1000, windowEnd.getTime() - windowStart.getTime());
    const postSlotMs = posts.length > 1 ? totalWindowMs / (posts.length - 1) : totalWindowMs / 2;

    const formattedPosts = posts.map((post, postIdx) => {
      const postId = `p_${postIdx + 1}`;
      const executions = [];

      profileExecutionOrder.forEach((pid, pIdx) => {
        // Find profile's slot for this post via its shuffled order
        const slotIdx = postingOrders[pid].indexOf(postIdx);
        const jitter = slotIdx === 0 && isStartNow
          ? 0
          : (Math.random() * (isStartNow ? 4 : 6) - (isStartNow ? 2 : 3)) * 60 * 1000;
        const scheduledTime = new Date(computeExecutionSchedule({
          nowMs: now.getTime(),
          isStartNow,
          slotIndex: slotIdx,
          profileIndex: pIdx,
          staggerMs: staggerSeconds * 1000,
          postSlotMs,
          windowStartMs: windowStart.getTime(),
          jitterMs: jitter,
        }));

        const spunCaption = post.ai_spin !== false
          ? generateSpunCaption(post.base_caption || '', pIdx, pid)
          : post.base_caption || '';

        executions.push({
          execution_id: `exec_${Date.now()}_${pIdx}_${postIdx}`,
          profile_id: pid,
          scheduled_at: scheduledTime.toISOString(),
          spun_caption: spunCaption,
          status: 'pending',
          stage: 'pending',
          stage_history: [{ stage: 'pending', timestamp: new Date().toISOString() }],
          preparation_mode: preparationMode,
          preparation_status: preparationMode === 'off' ? 'not_requested' : 'pending',
          retry_count: 0,
          error: null,
          published_at: null,
          logs: [],
        });
      });

      return {
        post_id: postId,
        type: post.type || 'photo',
        media_file: post.media_file || '',
        base_caption: post.base_caption || '',
        first_comment: post.first_comment || null,
        scrolls: Math.max(1, parseInt(post.scrolls, 10) || 4),
        ai_spin: post.ai_spin !== false,
        executions,
      };
    });

    const newBatch = {
      batch_id: batchId,
      name: name || `Daily Batch ${new Date().toLocaleDateString()}`,
      created_at: new Date().toISOString(),
      target_profiles: acceptedProfiles,
      profile_execution_order: profileExecutionOrder,
      schedule_window: {
        start_time: startTimeStr,
        end_time: endTimeStr,
        profile_stagger_seconds: staggerSeconds,
        session_preparation_mode: preparationMode,
        start_now: isStartNow,
      },
      posting_order_per_profile: postingOrders,
      posts: formattedPosts,
    };

    queue.daily_batches.unshift(newBatch);
    savePostingQueue(queue);

    if (isStartNow) {
      setTimeout(() => {
        dispatchPendingQueue().catch((e) => console.error('Dispatch now error:', e));
      }, 500);
    }

    res.json({
      success: true,
      batch_id: batchId,
      accepted_profiles: acceptedProfiles,
      skipped_profiles: skippedProfiles,
      total_executions: acceptedProfiles.length * posts.length,
      batch: newBatch,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/queue/batch/:batch_id
app.delete('/api/queue/batch/:batch_id', (req, res) => {
  try {
    const { batch_id } = req.params;
    const queue = loadPostingQueue();
    const targetBatch = (queue.daily_batches || []).find((batch) => batch.batch_id === batch_id);
    if (!targetBatch) {
      return res.status(404).json({ error: 'Batch not found' });
    }
    if (batchContainsUnresolvedExecution(targetBatch)) {
      return res.status(409).json({
        error: 'Cannot delete a batch containing active, uncertain, or needs-review executions. Stop safely or resolve every ambiguous outcome first.',
      });
    }
    queue.daily_batches = (queue.daily_batches || []).filter((b) => b.batch_id !== batch_id);
    savePostingQueue(queue);
    res.json({ success: true, message: `Batch ${batch_id} removed` });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/queue/execution/:execution_id
app.delete('/api/queue/execution/:execution_id', (req, res) => {
  try {
    const { execution_id } = req.params;
    const queue = loadPostingQueue();
    let found = false;
    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        const idx = (post.executions || []).findIndex((e) => e.execution_id === execution_id);
        if (idx !== -1) {
          if (isExecutionDeletionLocked(post.executions[idx])) {
            return res.status(409).json({
              error: 'Cannot delete an active or unresolved execution. Stop safely or resolve its publication outcome first.',
            });
          }
          post.executions.splice(idx, 1);
          found = true;
          break;
        }
      }
      if (found) break;
    }
    if (found) {
      savePostingQueue(queue);
      res.json({ success: true, message: `Execution ${execution_id} removed` });
    } else {
      res.status(404).json({ error: 'Execution not found' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

function claimQueueExecution(executionId, kind = 'publisher') {
  return withQueueClaimLock(() => {
    const queue = loadPostingQueue();
    const match = findQueueExecution(queue, executionId);
    if (!match) throw new Error('Execution not found');
    const { execution: targetExec, post: targetPost } = match;
    const standaloneWarming = targetPost.type === 'warming';
    const preparationMode = targetExec.preparation_mode || 'off';
    if (kind === 'preparer' && !standaloneWarming && (preparationMode === 'off' || targetExec.preparation_status !== 'pending')) {
      const error = new Error('Execution does not require passive preparation');
      error.schedulerReason = 'preparation_not_pending';
      throw error;
    }
    if (kind === 'publisher' && preparationMode !== 'off' && targetExec.preparation_status !== 'ready') {
      const error = new Error('Execution is not ready for the publisher slot');
      error.schedulerReason = 'preparation_not_ready';
      throw error;
    }
    const leaseId = randomUUID();
    const claim = claimExecutionLease(queue, targetExec, {
      leaseId,
      ownerId: MANAGER_INSTANCE_ID,
      kind,
      config: SCHEDULER_CONFIG,
      additionalLeases: activeInMemorySchedulerLeases(),
    });
    if (!claim.claimed) {
      const error = new Error(`Scheduler could not claim execution: ${claim.reason}`);
      error.schedulerReason = claim.reason;
      throw error;
    }

    const now = new Date().toISOString();
    targetExec.status = kind === 'preparer' && !standaloneWarming ? 'preparing' : 'running';
    targetExec.started_at = now;
    targetExec.ended_at = null;
    targetExec.error = null;
    targetExec.logs = [];
    targetExec.stage_history = Array.isArray(targetExec.stage_history) ? targetExec.stage_history : [];
    targetExec.stage = standaloneWarming ? 'warming' : 'preparing';
    targetExec.stage_history.push({
      stage: targetExec.stage,
      timestamp: now,
      reason: `${kind}_scheduler_lease_claimed`,
      lease_id: leaseId,
    });
    targetExec.stage_updated_at = now;
    if (!savePostingQueue(queue)) throw new Error('Failed to persist scheduler claim');
    return { targetExec, targetPost, leaseId, lease: claim.lease };
  });
}

async function executeQueuePreparation(executionId) {
  const { targetExec, leaseId, lease } = claimQueueExecution(executionId, 'preparer');
  const profileId = targetExec.profile_id;
  const pythonBin = path.join(ROOT_DIR, 'automation', 'venv', 'bin', 'python');
  const runnerScript = path.join(ROOT_DIR, 'automation', 'runner.py');
  const args = [
    '-u', runnerScript,
    '--profile', profileId,
    '--task', 'preparation',
    '--preparation-mode', targetExec.preparation_mode,
  ];
  const taskRecord = {
    profile_id: profileId,
    task: 'preparation',
    status: 'running',
    started_at: new Date().toISOString(),
    ended_at: null,
    logs: [],
    result: null,
    current_stage: 'preparing',
    error: null,
    process: null,
    lifecycle_active: true,
    queue_execution_id: executionId,
    scheduler_lease: lease,
    last_activity_at: new Date().toISOString(),
  };
  activeAutomationTasks.set(profileId, taskRecord);
  try {
    const lifecycle = await ensureProfileContainerReady(profileId);
    taskRecord.container_started_by_queue = lifecycle.started;
    taskRecord.last_activity_at = new Date().toISOString();
    taskRecord.lifecycle_active = false;
  } catch (error) {
    taskRecord.lifecycle_active = false;
    taskRecord.status = 'failed';
    taskRecord.error = error.message;
    taskRecord.scheduler_lease = null;
    taskRecord.ended_at = new Date().toISOString();
    const queue = loadPostingQueue();
    const match = findQueueExecution(queue, executionId);
    if (match && match.execution.scheduler_lease?.lease_id === leaseId) {
      match.execution.status = 'failed_before_publish';
      match.execution.stage = 'failed_before_publish';
      match.execution.preparation_status = 'failed';
      match.execution.error = `Container startup failed: ${error.message}`;
      match.execution.ended_at = taskRecord.ended_at;
      releaseExecutionLease(match.execution, leaseId);
      savePostingQueue(queue);
    }
    try { await stopProfileContainerAfterReport(profileId); } catch (_) {}
    setTimeout(() => dispatchPendingQueue().catch((dispatchError) => console.error('Post-startup-failure dispatch error:', dispatchError)), 100);
    return { success: false, status: 'failed_before_publish', error: error.message };
  }
  const proc = spawn(pythonBin, args, {
    cwd: ROOT_DIR,
    env: automationWorkerEnv(),
  });
  taskRecord.process = proc;

  let stdoutBuffer = '';
  const consumeLine = (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object' && ('status' in parsed || 'success' in parsed)) {
          taskRecord.result = parsed;
          return;
        }
      } catch (_) {}
    }
    taskRecord.logs.push(normalizeStoredLog(trimmed, profileId));
    const stageMatch = trimmed.match(/^\[(.*?)\]\s*\[(.*?)\]\s*\[STAGE\]\s*Execution stage:\s*([a-z_]+)\s*$/i);
    if (stageMatch) {
      taskRecord.current_stage = stageMatch[3];
      persistQueueExecutionStage(executionId, stageMatch[3], stageMatch[1], leaseId);
    }
  };
  proc.stdout.on('data', (chunk) => {
    taskRecord.last_activity_at = new Date().toISOString();
    stdoutBuffer += chunk.toString();
    const lines = stdoutBuffer.split('\n');
    stdoutBuffer = lines.pop();
    for (const line of lines) consumeLine(line);
  });
  proc.stderr.on('data', (chunk) => {
    taskRecord.last_activity_at = new Date().toISOString();
    for (const line of chunk.toString().split('\n')) {
      if (line.trim()) taskRecord.logs.push(normalizeStoredLog(`[STDERR] ${line.trim()}`, profileId));
    }
  });

  return new Promise((resolve) => {
    proc.on('close', async (code) => {
      if (stdoutBuffer.trim()) consumeLine(stdoutBuffer);
      taskRecord.ended_at = new Date().toISOString();
      taskRecord.process = null;
      const queue = loadPostingQueue();
      const match = findQueueExecution(queue, executionId);
      if (!match || match.execution.scheduler_lease?.lease_id !== leaseId) {
        taskRecord.status = 'fenced';
        resolve({ success: false, status: 'fenced', code });
        return;
      }
      const execution = match.execution;
      const reportedStatus = taskRecord.result?.status;
      if (code === 0 && reportedStatus === 'completed') {
        execution.status = 'ready';
        execution.stage = 'ready';
        execution.preparation_status = 'ready';
        execution.preparation_completed_at = new Date().toISOString();
        execution.error = null;
        taskRecord.status = 'completed';
      } else if (reportedStatus?.startsWith('skipped_')) {
        execution.status = reportedStatus;
        execution.stage = reportedStatus;
        execution.preparation_status = reportedStatus;
        execution.error = taskRecord.result?.error || null;
        taskRecord.status = reportedStatus;
      } else if (reportedStatus === 'needs_review' || reportedStatus === 'uncertain' || code === 2) {
        execution.status = 'needs_review';
        execution.stage = 'needs_review';
        execution.preparation_status = 'needs_review';
        execution.error = taskRecord.result?.error || 'Passive preparation requires operator review';
        taskRecord.status = 'needs_review';
      } else {
        execution.status = 'failed_before_publish';
        execution.stage = 'failed_before_publish';
        execution.preparation_status = 'failed';
        execution.error = taskRecord.result?.error || `Preparation exited with code ${code}`;
        taskRecord.status = 'failed';
      }
      execution.preparation_evidence_dir = taskRecord.result?.evidence_dir || null;
      execution.preparation_telemetry = taskRecord.result?.telemetry || null;
      applyWarmingResult(execution, taskRecord.result);
      execution.stage_history = Array.isArray(taskRecord.result?.stage_history)
        ? taskRecord.result.stage_history
        : execution.stage_history;
      releaseExecutionLease(execution, leaseId);
      savePostingQueue(queue);
      if (execution.status !== 'ready') {
        let cleanup;
        let cleanupError = null;
        try {
          cleanup = await stopProfileContainerAfterReport(profileId);
        } catch (error) {
          cleanupError = error.message;
        }
        const cleanupQueue = loadPostingQueue();
        const cleanupMatch = findQueueExecution(cleanupQueue, executionId);
        if (cleanupMatch) {
          cleanupMatch.execution.container_stopped_at = cleanup?.stopped ? new Date().toISOString() : null;
          cleanupMatch.execution.container_cleanup = cleanup || null;
          cleanupMatch.execution.container_cleanup_error = cleanupError;
          savePostingQueue(cleanupQueue);
        }
      }
      setTimeout(() => dispatchPendingQueue().catch((error) => console.error('Post-preparation dispatch error:', error)), 100);
      resolve({ success: execution.status === 'ready', status: execution.status, code });
    });
    proc.on('error', async (error) => {
      taskRecord.status = 'failed';
      taskRecord.error = error.message;
      taskRecord.process = null;
      const queue = loadPostingQueue();
      const match = findQueueExecution(queue, executionId);
      if (match && match.execution.scheduler_lease?.lease_id === leaseId) {
        applyInterruptedExecutionRecovery(match.execution);
        match.execution.preparation_status = 'failed';
        releaseExecutionLease(match.execution, leaseId);
        savePostingQueue(queue);
      }
      try { await stopProfileContainerAfterReport(profileId); } catch (_) {}
      setTimeout(() => dispatchPendingQueue().catch((dispatchError) => console.error('Post-preparation-error dispatch error:', dispatchError)), 100);
      resolve({ success: false, error: error.message });
    });
  });
}

// Trigger a queue execution
async function executeQueueItem(executionId, schedulerKind = 'publisher') {
  const { targetExec, targetPost, leaseId, lease } = claimQueueExecution(executionId, schedulerKind);
  const profileId = targetExec.profile_id;

  const pythonBin = path.join(ROOT_DIR, 'automation', 'venv', 'bin', 'python');
  const runnerScript = path.join(ROOT_DIR, 'automation', 'runner.py');
  const taskType = targetPost.type === 'warming' ? 'warming' : (targetPost.type === 'reel' ? 'reel' : 'post');

  const args = ['-u', runnerScript, '--profile', profileId, '--task', taskType];
  if (taskType === 'warming') {
    args.push('--scrolls', String(targetPost.scrolls || 4));
  }
  if (targetExec.spun_caption) {
    args.push('--caption', targetExec.spun_caption);
  }
  if (targetPost.first_comment) {
    args.push('--comment-link', targetPost.first_comment);
  }
  if (targetPost.media_file) {
    args.push('--media', targetPost.media_file);
  }

  const taskRecord = {
    profile_id: profileId,
    task: taskType,
    status: 'running',
    started_at: new Date().toISOString(),
    ended_at: null,
    logs: [],
    result: null,
    current_stage: targetExec.stage,
    queue_execution_id: executionId,
    scheduler_lease: lease,
    last_activity_at: new Date().toISOString(),
    error: null,
    process: null,
    lifecycle_active: true,
  };

  activeAutomationTasks.set(profileId, taskRecord);
  try {
    const lifecycle = await ensureProfileContainerReady(profileId);
    taskRecord.container_started_by_queue = lifecycle.started;
    taskRecord.last_activity_at = new Date().toISOString();
    taskRecord.lifecycle_active = false;
  } catch (error) {
    taskRecord.lifecycle_active = false;
    taskRecord.status = 'failed';
    taskRecord.error = error.message;
    taskRecord.ended_at = new Date().toISOString();
    const queue = loadPostingQueue();
    const match = findQueueExecution(queue, executionId);
    if (match && match.execution.scheduler_lease?.lease_id === leaseId) {
      match.execution.status = 'failed_before_publish';
      match.execution.stage = 'failed_before_publish';
      match.execution.error = `Container startup failed: ${error.message}`;
      match.execution.ended_at = taskRecord.ended_at;
      releaseExecutionLease(match.execution, leaseId);
      savePostingQueue(queue);
    }
    try { await stopProfileContainerAfterReport(profileId); } catch (_) {}
    setTimeout(() => dispatchPendingQueue().catch((dispatchError) => console.error('Post-startup-failure dispatch error:', dispatchError)), 100);
    return { success: false, status: 'failed_before_publish', error: error.message };
  }

  const proc = spawn(pythonBin, args, {
    cwd: ROOT_DIR,
    env: automationWorkerEnv(),
  });

  taskRecord.process = proc;

  let queueStdoutBuffer = '';
  const consumeQueueStdoutLine = (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed && typeof parsed === 'object' && ('status' in parsed || 'success' in parsed)) {
            taskRecord.result = parsed;
            return;
          }
        } catch (_) {}
      }
      targetExec.logs.push(trimmed);
      taskRecord.logs.push(normalizeStoredLog(trimmed, profileId));
      const stageMatch = trimmed.match(/^\[(.*?)\]\s*\[(.*?)\]\s*\[STAGE\]\s*Execution stage:\s*([a-z_]+)\s*$/i);
      if (stageMatch) {
        taskRecord.current_stage = stageMatch[3];
        persistQueueExecutionStage(executionId, stageMatch[3], stageMatch[1], leaseId);
      }
  };

  proc.stdout.on('data', (chunk) => {
    taskRecord.last_activity_at = new Date().toISOString();
    queueStdoutBuffer += chunk.toString();
    const lines = queueStdoutBuffer.split('\n');
    queueStdoutBuffer = lines.pop();
    for (const line of lines) {
      consumeQueueStdoutLine(line);
    }
  });

  proc.stderr.on('data', (chunk) => {
    taskRecord.last_activity_at = new Date().toISOString();
    const lines = chunk.toString().split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      targetExec.logs.push(`[STDERR] ${trimmed}`);
      taskRecord.logs.push({ timestamp: new Date().toISOString(), profile_id: profileId, level: 'WARN', message: trimmed });
    }
  });

  return new Promise((resolve) => {
    proc.on('close', async (code) => {
      if (queueStdoutBuffer.trim()) consumeQueueStdoutLine(queueStdoutBuffer);
      taskRecord.ended_at = new Date().toISOString();
      const reportedStatus = taskRecord.result?.status;
      const stage = taskRecord.result?.current_stage || taskRecord.current_stage;
      const reachedPublish = stage === 'publish_clicked' || stage === 'verifying';

      taskRecord.status = reportedStatus?.startsWith('skipped_')
        ? reportedStatus
        : (reportedStatus === 'needs_review' || reportedStatus === 'uncertain' || reachedPublish)
          ? (reportedStatus === 'needs_review' ? 'needs_review' : 'uncertain')
          : (code === 0 ? 'completed' : 'failed');
      taskRecord.process = null;

      const updatedQueue = loadPostingQueue();
      let execInDb = null;
      for (const batch of updatedQueue.daily_batches || []) {
        for (const post of batch.posts || []) {
          const match = (post.executions || []).find((e) => e.execution_id === executionId);
          if (match) {
            execInDb = match;
            break;
          }
        }
        if (execInDb) break;
      }

      if (execInDb) {
        if (execInDb.scheduler_lease?.lease_id !== leaseId) {
          console.warn(`[Scheduler] Ignored fenced completion for ${executionId}; lease token no longer matches.`);
          resolve({ success: false, status: 'fenced', code });
          return;
        }
        execInDb.ended_at = new Date().toISOString();
        execInDb.stage = stage || (reportedStatus === 'published' ? 'published' : 'unknown');
        if (Array.isArray(taskRecord.result?.stage_history)) {
          execInDb.stage_history = taskRecord.result.stage_history;
        }

        if (taskType === 'warming' && code === 0 && reportedStatus === 'completed') {
          execInDb.status = 'completed';
          execInDb.stage = 'completed';
          execInDb.error = null;
          applyWarmingResult(execInDb, taskRecord.result);
        } else if (reportedStatus?.startsWith('skipped_')) {
          execInDb.status = reportedStatus;
          execInDb.stage = reportedStatus;
          execInDb.error = taskRecord.result?.error || null;
        } else if (reportedStatus === 'published') {
          execInDb.status = 'published';
          execInDb.published_at = new Date().toISOString();
          execInDb.error = null;
          applyAutomationPermalinkResult(execInDb, taskRecord.result, taskType);
          if (taskRecord.result?.first_comment) {
            execInDb.first_comment_status = taskRecord.result.first_comment;
            execInDb.first_comment_method = taskRecord.result.first_comment_method || null;
            if (taskRecord.result.first_comment === 'submitted_verified') {
              execInDb.first_comment_verified_at = new Date().toISOString();
            }
          }
        } else if (reportedStatus === 'needs_review' || reportedStatus === 'uncertain' || code === 2 || reachedPublish) {
          // Never automatically retry after a one-way publish click or gated semantic fallback.
          execInDb.status = reportedStatus === 'needs_review' ? 'needs_review' : 'uncertain';
          execInDb.error = taskRecord.result?.error || 'Publication requires operator review or could not be visually confirmed';
        } else {
          execInDb.retry_count = (execInDb.retry_count || 0) + 1;
          execInDb.status = 'failed_before_publish';
          execInDb.error = taskRecord.result?.error
            || (code === 0
              ? 'Runner exited without a confirmed published outcome'
              : `Runner exited with exit code ${code}`);
        }
        if (taskRecord.result?.evidence_dir) {
          execInDb.evidence_dir = taskRecord.result.evidence_dir;
        }
        if (taskRecord.result?.telemetry) {
          execInDb.telemetry = taskRecord.result.telemetry;
        }
        execInDb.logs = targetExec.logs;
        releaseExecutionLease(execInDb, leaseId);
        savePostingQueue(updatedQueue);
        let cleanup;
        let cleanupError = null;
        try {
          cleanup = await stopProfileContainerAfterReport(profileId);
        } catch (error) {
          cleanupError = error.message;
        }
        const cleanupQueue = loadPostingQueue();
        const cleanupMatch = findQueueExecution(cleanupQueue, executionId);
        if (cleanupMatch) {
          cleanupMatch.execution.container_stopped_at = cleanup?.stopped ? new Date().toISOString() : null;
          cleanupMatch.execution.container_cleanup = cleanup || null;
          cleanupMatch.execution.container_cleanup_error = cleanupError;
          savePostingQueue(cleanupQueue);
        }
        setTimeout(() => dispatchPendingQueue().catch((error) => console.error('Post-publish dispatch error:', error)), 100);
      }
      resolve({ success: ['published', 'completed'].includes(execInDb?.status), status: execInDb?.status, code });
    });

    proc.on('error', async (err) => {
      taskRecord.ended_at = new Date().toISOString();
      taskRecord.status = 'failed';
      taskRecord.error = err.message;
      taskRecord.process = null;
      const failedQueue = loadPostingQueue();
      const failedMatch = findQueueExecution(failedQueue, executionId);
      if (failedMatch && failedMatch.execution.scheduler_lease?.lease_id === leaseId) {
        applyInterruptedExecutionRecovery(failedMatch.execution);
        failedMatch.execution.error = `Automation process error: ${err.message}. ${failedMatch.execution.error}`;
        releaseExecutionLease(failedMatch.execution, leaseId);
        savePostingQueue(failedQueue);
      }
      try { await stopProfileContainerAfterReport(profileId); } catch (_) {}
      setTimeout(() => dispatchPendingQueue().catch((error) => console.error('Post-publish-error dispatch error:', error)), 100);
      resolve({ success: false, error: err.message });
    });
  });
}

// POST /api/queue/run-now/:execution_id
app.post('/api/queue/run-now/:execution_id', async (req, res) => {
  try {
    const { execution_id } = req.params;
    const queue = loadPostingQueue();
    let targetExec = null;
    let targetPost = null;

    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        const match = (post.executions || []).find((e) => e.execution_id === execution_id);
        if (match) {
          targetExec = match;
          targetPost = post;
          break;
        }
      }
      if (targetExec) break;
    }

    if (!targetExec) {
      return res.status(404).json({ error: 'Execution not found' });
    }

    if (targetExec.status === 'uncertain' || targetExec.status === 'needs_review') {
      return res.status(400).json({
        error: `Cannot rerun an execution in '${targetExec.status}' state directly. Please review on Facebook and resolve the outcome first to prevent duplicate posts.`,
      });
    }

    // A skipped authentication preflight is safe to retry manually after the
    // operator logs the profile back in. It must not retry automatically.
    if (targetExec.status?.startsWith('skipped_')) {
      const now = new Date().toISOString();
      targetExec.status = 'pending';
      targetExec.stage = 'pending';
      targetExec.error = null;
      if ((targetExec.preparation_mode || 'off') !== 'off') {
        targetExec.preparation_status = 'pending';
      }
      targetExec.stage_history = Array.isArray(targetExec.stage_history) ? targetExec.stage_history : [];
      targetExec.stage_history.push({
        stage: 'pending',
        timestamp: now,
        reason: 'manual_retry_after_safe_skip',
      });
      savePostingQueue(queue);
    }

    const standaloneWarming = targetPost?.type === 'warming';
    const needsPreparation = (
      !standaloneWarming
      &&
      targetExec.status === 'pending'
      && (targetExec.preparation_mode || 'off') !== 'off'
      && targetExec.preparation_status === 'pending'
    );
    if (
      needsPreparation
      && countBufferedPreparations(queue, execution_id) >= SCHEDULER_CONFIG.max_preparers
    ) {
      return res.status(409).json({ error: 'All preparation buffers are currently occupied.' });
    }
    if (
      getContainerStatus(targetExec.profile_id) !== 'running'
      && countRunningProfileContainers() >= SCHEDULER_CONFIG.max_active_profile_containers
    ) {
      return res.status(409).json({ error: 'The active profile-container limit has been reached.' });
    }
    if (getContainerStatus(targetExec.profile_id) !== 'running') {
      const admission = claimContainerStartAdmission();
      if (!admission.allowed) {
        return res.status(409).json({ error: `Container start delayed: ${admission.reason}.` });
      }
    }
    const slotKind = (needsPreparation || standaloneWarming) ? 'preparer' : 'publisher';
    const executionPromise = needsPreparation
      ? executeQueuePreparation(execution_id)
      : executeQueueItem(execution_id, slotKind);
    executionPromise.catch((err) => console.error('Run-now error:', err));
    res.json({
      success: true,
      message: needsPreparation
        ? `Started passive preparation for execution ${execution_id}`
        : standaloneWarming
          ? `Dispatched feed warming ${execution_id} immediately`
          : `Dispatched execution ${execution_id} immediately`,
    });
  } catch (err) {
    if (err.schedulerReason) {
      return res.status(409).json({ error: err.message, scheduler_reason: err.schedulerReason });
    }
    res.status(500).json({ error: err.message });
  }
});

// POST /api/queue/resolve-uncertain/:execution_id
app.post('/api/queue/resolve-uncertain/:execution_id', async (req, res) => {
  try {
    const { execution_id } = req.params;
    const { resolution, note, post_url } = req.body;

    if (!['published', 'not_published'].includes(resolution)) {
      return res.status(400).json({ error: "Resolution must be either 'published' or 'not_published'" });
    }

    const queue = loadPostingQueue();
    let targetExec = null;
    let targetPost = null;
    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        const match = (post.executions || []).find((e) => e.execution_id === execution_id);
        if (match) {
          targetExec = match;
          targetPost = post;
          break;
        }
      }
      if (targetExec) break;
    }

    if (!targetExec) {
      return res.status(404).json({ error: 'Execution not found' });
    }

    if (targetExec.status !== 'uncertain' && targetExec.status !== 'needs_review') {
      return res.status(400).json({ error: `Execution is in '${targetExec.status}' state, not 'uncertain' or 'needs_review'` });
    }

    let validatedPostUrl = null;
    if (resolution === 'published' && post_url && typeof post_url === 'string' && post_url.trim()) {
      validatedPostUrl = validateFacebookPermalink(
        post_url,
        targetPost?.type === 'reel' ? 'reel' : 'post'
      );
      if (!validatedPostUrl) {
        return res.status(400).json({
          error: 'The supplied URL is not a recognized Facebook post or reel permalink.',
        });
      }
    }

    const reviewedAt = new Date().toISOString();
    targetExec.stage_history = Array.isArray(targetExec.stage_history) ? targetExec.stage_history : [];

    if (resolution === 'published') {
      targetExec.status = 'published';
      targetExec.stage = 'published';
      targetExec.published_at = reviewedAt;
      targetExec.review_status = 'resolved_published';
      targetExec.review_note = note || 'Manually confirmed published on Facebook';
      targetExec.error = null;
      if (validatedPostUrl) {
        targetExec.post_url = validatedPostUrl;
        targetExec.post_url_verified_at = new Date().toISOString();
        targetExec.post_match_confidence = 1.0;
      }
    } else {
      // Operator verified it was NOT published. Reset to failed_before_publish so retry is permitted.
      targetExec.status = 'failed_before_publish';
      targetExec.stage = 'failed_before_publish';
      targetExec.review_status = 'resolved_not_published';
      targetExec.review_note = note || 'Manually confirmed NOT published on Facebook';
    }

    targetExec.stage_history.push({
      stage: targetExec.stage,
      timestamp: reviewedAt,
      reason: `operator_resolved_${resolution}`,
    });
    targetExec.stage_updated_at = reviewedAt;
    targetExec.reviewed_at = reviewedAt;
    savePostingQueue(queue);

    res.json({
      success: true,
      message: `Execution resolved as ${resolution}`,
      execution: targetExec,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/queue/backfill-permalink/:execution_id
app.post('/api/queue/backfill-permalink/:execution_id', (req, res) => {
  try {
    const { execution_id } = req.params;
    const { post_url, note, match_confidence, source } = req.body || {};
    const queue = loadPostingQueue();
    const match = findQueueExecution(queue, execution_id);
    if (!match) {
      return res.status(404).json({ error: 'Execution not found' });
    }
    try {
      applyVerifiedPermalinkBackfill(
        match.execution,
        post_url,
        match.post?.type === 'reel' ? 'reel' : 'post',
        {
          note,
          matchConfidence: match_confidence,
          source: source || 'manual_backfill',
        },
      );
    } catch (error) {
      return res.status(400).json({ error: error.message });
    }
    savePostingQueue(queue);
    return res.json({
      success: true,
      message: 'Verified permalink attached to published execution',
      execution: match.execution,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

// POST /api/queue/backfill-comment/:execution_id
app.post('/api/queue/backfill-comment/:execution_id', (req, res) => {
  try {
    const { execution_id } = req.params;
    const { status, note, evidence_dir, source } = req.body || {};
    const queue = loadPostingQueue();
    const match = findQueueExecution(queue, execution_id);
    if (!match) {
      return res.status(404).json({ error: 'Execution not found' });
    }
    try {
      applyCommentEvidenceBackfill(match.execution, status, {
        note,
        evidenceDir: evidence_dir,
        source: source || 'manual_backfill',
      });
    } catch (error) {
      return res.status(400).json({ error: error.message });
    }
    savePostingQueue(queue);
    return res.json({
      success: true,
      message: 'Comment evidence attached to published execution',
      execution: match.execution,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

async function executeQueueCommentRetry(executionId, startAdmissionClaimed = false) {
  const queue = loadPostingQueue();
  const match = findQueueExecution(queue, executionId);
  if (!match) throw new Error('Execution not found');

  const commentText = match.execution.first_comment || match.post?.first_comment || '';
  if (!canRetryFirstComment(match.execution, commentText)) {
    throw new Error('Comment retry is allowed only after a definite pre-submission input failure');
  }
  const profileId = match.execution.profile_id;
  const existing = activeAutomationTasks.get(profileId);
  if (isTaskProcessActive(existing)) {
    throw new Error(`Automation is already running for profile "${profileId}"`);
  }
  const slotCheck = canAcquireSchedulerSlot(
    queue,
    activeInMemorySchedulerLeases(),
    'publisher',
    profileId,
    SCHEDULER_CONFIG,
  );
  if (!slotCheck.allowed) {
    const error = new Error(`Scheduler capacity unavailable: ${slotCheck.reason}`);
    error.statusCode = 409;
    throw error;
  }
  if (
    getContainerStatus(profileId) !== 'running'
    && countRunningProfileContainers() >= SCHEDULER_CONFIG.max_active_profile_containers
  ) {
    const error = new Error('The active profile-container limit has been reached.');
    error.statusCode = 409;
    throw error;
  }
  if (getContainerStatus(profileId) !== 'running' && !startAdmissionClaimed) {
    const admission = claimContainerStartAdmission();
    if (!admission.allowed) {
      const error = new Error(`Container start delayed: ${admission.reason}.`);
      error.statusCode = 409;
      throw error;
    }
  }

  const nowMs = Date.now();
  const lease = {
    lease_id: randomUUID(),
    owner_id: MANAGER_INSTANCE_ID,
    kind: 'publisher',
    profile_id: profileId,
    claimed_at: new Date(nowMs).toISOString(),
    heartbeat_at: new Date(nowMs).toISOString(),
    expires_at: new Date(nowMs + SCHEDULER_CONFIG.lease_ttl_ms).toISOString(),
  };
  match.execution.comment_retry_status = 'running';
  match.execution.comment_retry_started_at = lease.claimed_at;
  match.execution.first_comment_note = null;
  savePostingQueue(queue);

  const taskRecord = {
    profile_id: profileId,
    task: 'comment',
    comment_retry: true,
    queue_execution_id: executionId,
    status: 'running',
    started_at: lease.claimed_at,
    ended_at: null,
    logs: [],
    result: null,
    error: null,
    process: null,
    scheduler_lease: lease,
    last_activity_at: lease.claimed_at,
    lifecycle_active: true,
  };
  activeAutomationTasks.set(profileId, taskRecord);

  try {
    await ensureProfileContainerReady(profileId);
    taskRecord.lifecycle_active = false;
  } catch (error) {
    taskRecord.lifecycle_active = false;
    taskRecord.status = 'failed';
    taskRecord.error = error.message;
    const failedQueue = loadPostingQueue();
    const failedMatch = findQueueExecution(failedQueue, executionId);
    if (failedMatch) {
      failedMatch.execution.comment_retry_status = 'failed_to_start';
      failedMatch.execution.comment_retry_ended_at = new Date().toISOString();
      failedMatch.execution.first_comment_note = `Comment retry could not start: ${error.message}`;
      savePostingQueue(failedQueue);
    }
    try { await stopProfileContainerAfterReport(profileId); } catch (_) {}
    throw error;
  }

  const pythonBin = path.join(ROOT_DIR, 'automation', 'venv', 'bin', 'python');
  const runnerScript = path.join(ROOT_DIR, 'automation', 'runner.py');
  const args = [
    '-u', runnerScript,
    '--profile', profileId,
    '--task', 'comment',
    '--comment-link', commentText,
  ];
  if (match.execution.post_url) args.push('--post-url', match.execution.post_url);

  const proc = spawn(pythonBin, args, { cwd: ROOT_DIR, env: automationWorkerEnv() });
  taskRecord.process = proc;
  let stdoutBuffer = '';
  proc.stdout.on('data', (chunk) => {
    taskRecord.last_activity_at = new Date().toISOString();
    stdoutBuffer += chunk.toString();
    const lines = stdoutBuffer.split('\n');
    stdoutBuffer = lines.pop();
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed && typeof parsed === 'object' && 'status' in parsed) {
            taskRecord.result = parsed;
            continue;
          }
        } catch (_) {}
      }
      taskRecord.logs.push(normalizeStoredLog(trimmed, profileId));
    }
  });
  proc.stderr.on('data', (chunk) => {
    taskRecord.last_activity_at = new Date().toISOString();
    for (const line of chunk.toString().split('\n')) {
      if (line.trim()) taskRecord.logs.push(normalizeStoredLog(`[STDERR] ${line.trim()}`, profileId));
    }
  });

  return new Promise((resolve) => {
    proc.on('close', async (code) => {
      if (stdoutBuffer.trim()) {
        try {
          const parsed = JSON.parse(stdoutBuffer.trim());
          if (parsed && typeof parsed === 'object') taskRecord.result = parsed;
        } catch (_) {
          taskRecord.logs.push(normalizeStoredLog(stdoutBuffer.trim(), profileId));
        }
      }
      const endedAt = new Date().toISOString();
      taskRecord.ended_at = endedAt;
      taskRecord.process = null;
      taskRecord.scheduler_lease = null;
      taskRecord.status = code === 0 ? 'completed' : (taskRecord.result?.status || 'failed');

      const updatedQueue = loadPostingQueue();
      const updatedMatch = findQueueExecution(updatedQueue, executionId);
      if (updatedMatch) {
        applyCommentRetryResult(updatedMatch.execution, taskRecord.result || {}, endedAt);
        updatedMatch.execution.first_comment_note = code === 0
          ? null
          : (taskRecord.result?.error || `Comment retry exited with code ${code}`);
        updatedMatch.execution.logs = [
          ...(updatedMatch.execution.logs || []),
          ...taskRecord.logs.map((entry) => `[Comment retry] ${entry.message}`),
        ].slice(-500);
        savePostingQueue(updatedQueue);
      }
      try { await stopProfileContainerAfterReport(profileId); } catch (_) {}
      resolve({ success: code === 0, status: updatedMatch?.execution?.first_comment_status || 'failed' });
    });
    proc.on('error', async (error) => {
      taskRecord.status = 'failed';
      taskRecord.error = error.message;
      taskRecord.process = null;
      taskRecord.scheduler_lease = null;
      const failedQueue = loadPostingQueue();
      const failedMatch = findQueueExecution(failedQueue, executionId);
      if (failedMatch) {
        failedMatch.execution.comment_retry_status = 'failed_to_start';
        failedMatch.execution.comment_retry_ended_at = new Date().toISOString();
        failedMatch.execution.first_comment_note = error.message;
        savePostingQueue(failedQueue);
      }
      try { await stopProfileContainerAfterReport(profileId); } catch (_) {}
      resolve({ success: false, status: 'failed_to_start', error: error.message });
    });
  });
}

// POST /api/queue/retry-comment/:execution_id
app.post('/api/queue/retry-comment/:execution_id', (req, res) => {
  try {
    const { execution_id } = req.params;
    const queue = loadPostingQueue();
    const match = findQueueExecution(queue, execution_id);
    if (!match) return res.status(404).json({ error: 'Execution not found' });
    const commentText = match.execution.first_comment || match.post?.first_comment || '';
    if (!canRetryFirstComment(match.execution, commentText)) {
      return res.status(400).json({
        error: 'Comment retry is allowed only after a definite pre-submission input failure.',
      });
    }
    const profileId = match.execution.profile_id;
    if (isTaskProcessActive(activeAutomationTasks.get(profileId))) {
      return res.status(409).json({ error: `Automation is already running for profile "${profileId}"` });
    }
    const slotCheck = canAcquireSchedulerSlot(
      queue,
      activeInMemorySchedulerLeases(),
      'publisher',
      profileId,
      SCHEDULER_CONFIG,
    );
    if (!slotCheck.allowed) {
      return res.status(409).json({ error: `Scheduler capacity unavailable: ${slotCheck.reason}` });
    }
    if (
      getContainerStatus(profileId) !== 'running'
      && countRunningProfileContainers() >= SCHEDULER_CONFIG.max_active_profile_containers
    ) {
      return res.status(409).json({ error: 'The active profile-container limit has been reached.' });
    }
    let startAdmissionClaimed = false;
    if (getContainerStatus(profileId) !== 'running') {
      const admission = claimContainerStartAdmission();
      if (!admission.allowed) {
        return res.status(409).json({ error: `Container start delayed: ${admission.reason}.` });
      }
      startAdmissionClaimed = true;
    }
    executeQueueCommentRetry(execution_id, startAdmissionClaimed).catch((error) => {
      console.error(`Comment retry failed for ${execution_id}:`, error);
    });
    return res.json({ success: true, message: 'Comment-only retry started' });
  } catch (error) {
    return res.status(error.statusCode || 500).json({ error: error.message });
  }
});

// Periodic Background Queue Dispatcher (Every 25 seconds)
let isDispatching = false;
async function dispatchPendingQueue() {
  if (isDispatching) return;
  isDispatching = true;
  try {
    recoverStaleQueueExecutions('dispatcher_stale_lease');
    const dueExecutionIds = orderedDueExecutions(loadPostingQueue(), Date.now())
      .map((execution) => execution.execution_id);

    for (const executionId of dueExecutionIds) {
      const currentQueue = loadPostingQueue();
      const match = findQueueExecution(currentQueue, executionId);
      if (!match || !['pending', 'ready'].includes(match.execution.status)) continue;
      const pid = match.execution.profile_id;

      const standaloneWarming = match.post.type === 'warming';
      const needsPreparation = (
        !standaloneWarming
        &&
        match.execution.status === 'pending'
        && (match.execution.preparation_mode || 'off') !== 'off'
        && match.execution.preparation_status === 'pending'
      );
      if (
        needsPreparation
        && countBufferedPreparations(currentQueue, executionId) >= SCHEDULER_CONFIG.max_preparers
      ) continue;
      if (
        getContainerStatus(pid) !== 'running'
        && countRunningProfileContainers() >= SCHEDULER_CONFIG.max_active_profile_containers
      ) continue;
      const slotKind = (needsPreparation || standaloneWarming) ? 'preparer' : 'publisher';
      const availability = canAcquireSchedulerSlot(
        currentQueue, activeInMemorySchedulerLeases(), slotKind, pid, SCHEDULER_CONFIG,
      );
      if (!availability.allowed) continue;

      if (getContainerStatus(pid) !== 'running') {
        const admission = claimContainerStartAdmission();
        if (!admission.allowed) {
          if (admission.reason === 'container_start_spacing') {
            setTimeout(() => dispatchPendingQueue().catch((error) => console.error('Spaced dispatch error:', error)), CONTAINER_START_SPACING_MS + 100);
          }
          continue;
        }
      }

      console.log(`[Queue Dispatcher] Claiming ${slotKind} slot for ${executionId} on profile ${pid}...`);
      try {
        const executionPromise = needsPreparation
          ? executeQueuePreparation(executionId)
          : executeQueueItem(executionId, slotKind);
        executionPromise.catch((e) => console.error('Dispatch error:', e));
      } catch (error) {
        if (!['publisher_capacity_reached', 'preparer_capacity_reached', 'profile_busy'].includes(error.schedulerReason)) {
          console.error('Dispatch claim error:', error);
        }
      }
    }
  } catch (err) {
    console.error('Queue Dispatcher error:', err);
  } finally {
    isDispatching = false;
  }
}

const queueDispatchTimer = setInterval(dispatchPendingQueue, 25000);
queueDispatchTimer.unref?.();

// ==========================================
// System Stats Endpoint
// ==========================================

// GET /api/system/stats
app.get('/api/system/stats', (req, res) => {
  try {
    let dockerRunning = false;
    let activeContainers = 0;

    try {
      execSync('docker info', { stdio: 'ignore' });
      dockerRunning = true;

      const runningOutput = execSync(
        'docker ps --filter "name=^/isolated_" -q',
        { encoding: 'utf-8' }
      ).trim();
      if (runningOutput) {
        activeContainers = runningOutput.split('\n').filter(Boolean).length;
      }
    } catch (_) {}

    const totalProfiles = fs.existsSync(PROFILES_DIR)
      ? fs.readdirSync(PROFILES_DIR).filter((d) => fs.existsSync(path.join(PROFILES_DIR, d, 'config.json'))).length
      : 0;

    const totalMem = Math.round(os.totalmem() / (1024 * 1024));
    const freeMem = Math.round(os.freemem() / (1024 * 1024));
    const usedMem = totalMem - freeMem;

    const proxyPool = loadProxyPool();
    const availableProxies = proxyPool.filter((p) => !p.assigned).length;

    res.json({
      docker_running: dockerRunning,
      active_profiles: activeContainers,
      total_profiles: totalProfiles,
      used_memory_mb: usedMem,
      total_memory_mb: totalMem,
      available_proxies: availableProxies,
      cpu_percent: currentCpuPercent,
      cpu_cores: cpuCores,
      cpu_model: cpuModel,
      gpu_percent: currentGpuPercent,
      gpu_model: gpuModel,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const httpServer = app.listen(PORT, '127.0.0.1', () => {
  console.log(`Manager backend bridge listening on http://127.0.0.1:${PORT}`);
  console.log(
    `[Scheduler] publishers=${SCHEDULER_CONFIG.max_publishers}, `
    + `preparers=${SCHEDULER_CONFIG.max_preparers}, `
    + `total=${SCHEDULER_CONFIG.max_total_automation_tasks}, `
    + `containers=${SCHEDULER_CONFIG.max_active_profile_containers}, `
    + `lease_ttl_ms=${SCHEDULER_CONFIG.lease_ttl_ms}`
  );
});

let shutdownStarted = false;
function gracefulShutdown(signal) {
  if (shutdownStarted) return;
  shutdownStarted = true;
  console.log(`[Shutdown] ${signal} received; stopping dispatcher and active automation workers...`);
  clearInterval(queueDispatchTimer);
  clearInterval(schedulerHeartbeatTimer);
  httpServer.close();

  for (const record of activeAutomationTasks.values()) {
    if (isTaskProcessActive(record)) {
      record.shutdown_requested_at = new Date().toISOString();
      try { record.process.kill('SIGTERM'); } catch (_) {}
    }
  }

  const deadline = Date.now() + 5_000;
  const shutdownPoll = setInterval(() => {
    const active = [...activeAutomationTasks.values()].filter(isTaskProcessActive);
    if (active.length === 0) {
      clearInterval(shutdownPoll);
      process.exit(0);
    }
    if (Date.now() >= deadline) {
      for (const record of active) {
        try { record.process.kill('SIGKILL'); } catch (_) {}
      }
      clearInterval(shutdownPoll);
      process.exit(1);
    }
  }, 100);
}

process.on('SIGINT', () => gracefulShutdown('SIGINT'));
process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
