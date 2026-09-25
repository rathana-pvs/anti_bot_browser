import express from 'express';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { exec, execSync, spawn } from 'child_process';
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

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, '..');
const PROFILES_DIR = path.join(ROOT_DIR, 'profiles');
const SCRIPTS_DIR = path.join(ROOT_DIR, 'scripts');
const DATA_DIR = path.join(ROOT_DIR, 'data');
const SHARED_MEDIA_DIR = path.join(PROFILES_DIR, 'shared_media');
const QUEUE_FILE = path.join(DATA_DIR, 'posting_queue.json');

if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
if (!fs.existsSync(SHARED_MEDIA_DIR)) fs.mkdirSync(SHARED_MEDIA_DIR, { recursive: true });

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
    }
  }
  prevCpuTimes = { idle, total };
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

// Periodic background samplers
setInterval(() => {
  sampleCpu();
  sampleGpu();
}, 2000);

setInterval(() => {
  sampleContainerStats();
}, 4000);

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

    res.json({ success: true });
  } catch (err) {
    console.error(`Failed to delete profile ${id}:`, err);
    res.status(500).json({ error: err.message });
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
          candidates.push({ execution, task: post.type === 'reel' ? 'reel' : 'post' });
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
    running: 'running',
    published: 'completed',
    failed: 'failed',
    uncertain: 'uncertain',
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
    };

    const proc = spawn(pythonBin, args, {
      cwd: ROOT_DIR,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
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
      stdoutBuffer += data.toString();
      const lines = stdoutBuffer.split('\n');
      stdoutBuffer = lines.pop();
      for (const line of lines) {
        appendLog(line, 'INFO');
      }
    });

    let stderrBuffer = '';
    proc.stderr.on('data', (data) => {
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

      if (taskRecord.status === 'stopped') {
        appendLog(`[${taskRecord.ended_at}] [${profile_id}] [INFO] Task stopped by user.`, 'INFO');
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

function cleanupStaleQueueExecutions() {
  try {
    const queue = loadPostingQueue();
    let modified = false;
    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        for (const execItem of post.executions || []) {
          if (execItem.status === 'running') {
            console.log(`[Startup] Cleaning up stale running execution ${execItem.execution_id}...`);
            execItem.status = 'stopped';
            execItem.ended_at = new Date().toISOString();
            execItem.error = 'Execution interrupted by server restart';
            modified = true;
          }
        }
      }
    }
    if (modified) {
      savePostingQueue(queue);
    }
  } catch (err) {
    console.error('Error during startup cleanup of posting queue:', err);
  }
}

cleanupStaleQueueExecutions();

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
          });
        });
      });
    });

    // Sort executions by scheduled_at ascending
    allExecutions.sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));

    const stats = {
      total: allExecutions.length,
      pending: allExecutions.filter((e) => e.status === 'pending').length,
      running: allExecutions.filter((e) => e.status === 'running').length,
      published: allExecutions.filter((e) => e.status === 'published').length,
      failed: allExecutions.filter((e) => e.status === 'failed' || e.status === 'failed_before_publish').length,
      uncertain: allExecutions.filter((e) => e.status === 'uncertain' || e.status === 'needs_review').length,
      skipped: allExecutions.filter((e) => e.status && e.status.startsWith('skipped')).length,
    };

    res.json({
      queue_version: queue.queue_version,
      stats,
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
    const staggerMins = parseInt(schedule_window?.profile_stagger_minutes, 10) || 15;

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

      acceptedProfiles.forEach((pid, pIdx) => {
        // Find profile's slot for this post via its shuffled order
        const slotIdx = postingOrders[pid].indexOf(postIdx);
        let scheduledTime;

        if (isStartNow && slotIdx === 0) {
          // First post slot starts immediately now for the lead profile, staggered for others
          scheduledTime = new Date(now.getTime() + pIdx * staggerMins * 60 * 1000);
        } else if (isStartNow) {
          const staggerOffset = pIdx * staggerMins * 60 * 1000;
          const jitter = (Math.random() * 4 - 2) * 60 * 1000;
          scheduledTime = new Date(now.getTime() + slotIdx * postSlotMs + staggerOffset + jitter);
        } else {
          const baseSlotTime = windowStart.getTime() + slotIdx * postSlotMs;
          const staggerOffset = pIdx * staggerMins * 60 * 1000;
          const jitter = (Math.random() * 6 - 3) * 60 * 1000;
          scheduledTime = new Date(Math.max(now.getTime() + 60000, baseSlotTime + staggerOffset + jitter));
        }

        const spunCaption = post.ai_spin !== false
          ? generateSpunCaption(post.base_caption || '', pIdx, pid)
          : post.base_caption || '';

        executions.push({
          execution_id: `exec_${Date.now()}_${pIdx}_${postIdx}`,
          profile_id: pid,
          scheduled_at: scheduledTime.toISOString(),
          spun_caption: spunCaption,
          status: 'pending',
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
        ai_spin: post.ai_spin !== false,
        executions,
      };
    });

    const newBatch = {
      batch_id: batchId,
      name: name || `Daily Batch ${new Date().toLocaleDateString()}`,
      created_at: new Date().toISOString(),
      target_profiles: acceptedProfiles,
      schedule_window: {
        start_time: startTimeStr,
        end_time: endTimeStr,
        profile_stagger_minutes: staggerMins,
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

// Trigger a queue execution
function executeQueueItem(executionId) {
  const queue = loadPostingQueue();
  let targetExec = null;
  let targetPost = null;

  for (const batch of queue.daily_batches || []) {
    for (const post of batch.posts || []) {
      const match = (post.executions || []).find((e) => e.execution_id === executionId);
      if (match) {
        targetExec = match;
        targetPost = post;
        break;
      }
    }
    if (targetExec) break;
  }

  if (!targetExec || !targetPost) return Promise.reject(new Error('Execution not found'));

  // Invariant: Uncertain and needs_review executions cannot be rerun directly without manual resolution
  if (targetExec.status === 'uncertain' || targetExec.status === 'needs_review') {
    return Promise.reject(new Error(`Cannot rerun an execution in '${targetExec.status}' state directly. Please review on Facebook and manually resolve the outcome first to prevent duplicate posts.`));
  }

  const profileId = targetExec.profile_id;

  // Check if profile is already running an automation task (only if actual OS process is active)
  const existing = activeAutomationTasks.get(profileId);
  const isProcessActive = existing && existing.status === 'running' && existing.process && !existing.process.killed && existing.process.exitCode === null;
  if (isProcessActive) {
    return Promise.reject(new Error(`Profile ${profileId} is currently busy with another task`));
  }

  // Update status to running
  targetExec.status = 'running';
  targetExec.started_at = new Date().toISOString();
  targetExec.ended_at = null;
  targetExec.error = null;
  targetExec.logs = [];
  savePostingQueue(queue);

  const pythonBin = path.join(ROOT_DIR, 'automation', 'venv', 'bin', 'python');
  const runnerScript = path.join(ROOT_DIR, 'automation', 'runner.py');
  const taskType = targetPost.type === 'reel' ? 'reel' : 'post';

  const args = ['-u', runnerScript, '--profile', profileId, '--task', taskType];
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
    error: null,
    process: null,
  };

  const proc = spawn(pythonBin, args, {
    cwd: ROOT_DIR,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  taskRecord.process = proc;
  activeAutomationTasks.set(profileId, taskRecord);

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
  };

  proc.stdout.on('data', (chunk) => {
    queueStdoutBuffer += chunk.toString();
    const lines = queueStdoutBuffer.split('\n');
    queueStdoutBuffer = lines.pop();
    for (const line of lines) {
      consumeQueueStdoutLine(line);
    }
  });

  proc.stderr.on('data', (chunk) => {
    const lines = chunk.toString().split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      targetExec.logs.push(`[STDERR] ${trimmed}`);
      taskRecord.logs.push({ timestamp: new Date().toISOString(), profile_id: profileId, level: 'WARN', message: trimmed });
    }
  });

  return new Promise((resolve) => {
    proc.on('close', (code) => {
      if (queueStdoutBuffer.trim()) consumeQueueStdoutLine(queueStdoutBuffer);
      taskRecord.ended_at = new Date().toISOString();
      const reportedStatus = taskRecord.result?.status;
      const stage = taskRecord.result?.current_stage;
      const reachedPublish = stage === 'publish_clicked' || stage === 'verifying';

      taskRecord.status = (reportedStatus === 'needs_review' || reportedStatus === 'uncertain' || reachedPublish)
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
        execInDb.ended_at = new Date().toISOString();
        execInDb.stage = stage || (reportedStatus === 'published' ? 'published' : 'unknown');

        if (reportedStatus === 'published') {
          execInDb.status = 'published';
          execInDb.published_at = new Date().toISOString();
          execInDb.error = null;
          if (taskRecord.result?.post_url) {
            execInDb.post_url = taskRecord.result.post_url;
            execInDb.post_url_verified_at = taskRecord.result.post_url_verified_at || new Date().toISOString();
            execInDb.post_match_confidence = taskRecord.result.post_match_confidence || 1.0;
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
        execInDb.logs = targetExec.logs;
        savePostingQueue(updatedQueue);
      }
      resolve({ success: execInDb?.status === 'published', status: execInDb?.status, code });
    });

    proc.on('error', (err) => {
      taskRecord.ended_at = new Date().toISOString();
      taskRecord.status = 'failed';
      taskRecord.error = err.message;
      taskRecord.process = null;
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

    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        const match = (post.executions || []).find((e) => e.execution_id === execution_id);
        if (match) {
          targetExec = match;
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

    executeQueueItem(execution_id).catch((err) => console.error('Run-now error:', err));
    res.json({ success: true, message: `Dispatched execution ${execution_id} immediately` });
  } catch (err) {
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
    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        const match = (post.executions || []).find((e) => e.execution_id === execution_id);
        if (match) {
          targetExec = match;
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

    if (resolution === 'published') {
      targetExec.status = 'published';
      targetExec.published_at = new Date().toISOString();
      targetExec.review_status = 'resolved_published';
      targetExec.review_note = note || 'Manually confirmed published on Facebook';
      targetExec.error = null;
      if (post_url && typeof post_url === 'string' && post_url.trim()) {
        targetExec.post_url = post_url.trim();
        targetExec.post_url_verified_at = new Date().toISOString();
        targetExec.post_match_confidence = 1.0;
      }
    } else {
      // Operator verified it was NOT published. Reset to failed_before_publish so retry is permitted.
      targetExec.status = 'failed_before_publish';
      targetExec.review_status = 'resolved_not_published';
      targetExec.review_note = note || 'Manually confirmed NOT published on Facebook';
    }

    targetExec.reviewed_at = new Date().toISOString();
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

// Periodic Background Queue Dispatcher (Every 25 seconds)
let isDispatching = false;
async function dispatchPendingQueue() {
  if (isDispatching) return;
  isDispatching = true;
  try {
    const queue = loadPostingQueue();
    const now = new Date();

    for (const batch of queue.daily_batches || []) {
      for (const post of batch.posts || []) {
        for (const execItem of post.executions || []) {
          if (execItem.status === 'pending' && new Date(execItem.scheduled_at) <= now) {
            const pid = execItem.profile_id;
            const containerName = `isolated_${pid}`;
            const isRunning = getContainerStatus(pid) === 'running';

            if (!isRunning) {
              execItem.status = 'skipped_stopped';
              execItem.error = 'Profile container was not running at scheduled time';
              savePostingQueue(queue);
              continue;
            }

            const active = activeAutomationTasks.get(pid);
            const isProcessActive = active && active.status === 'running' && active.process && !active.process.killed && active.process.exitCode === null;
            if (isProcessActive) {
              // Defer 2 minutes if profile is busy
              execItem.scheduled_at = new Date(Date.now() + 120000).toISOString();
              savePostingQueue(queue);
              continue;
            }

            // Launch execution
            console.log(`[Queue Dispatcher] Firing scheduled post ${execItem.execution_id} on profile ${pid}...`);
            executeQueueItem(execItem.execution_id).catch((e) => console.error('Dispatch error:', e));
          }
        }
      }
    }
  } catch (err) {
    console.error('Queue Dispatcher error:', err);
  } finally {
    isDispatching = false;
  }
}

setInterval(dispatchPendingQueue, 25000);

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

app.listen(PORT, '127.0.0.1', () => {
  console.log(`Manager backend bridge listening on http://127.0.0.1:${PORT}`);
});
