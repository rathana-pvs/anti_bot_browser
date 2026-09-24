import fs from 'fs';
import path from 'path';
import net from 'net';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, '..');
const PROXY_POOL_FILE = path.join(ROOT_DIR, 'proxies', 'proxy_pool.json');

export function loadProxyPool() {
  try {
    if (!fs.existsSync(PROXY_POOL_FILE)) {
      fs.mkdirSync(path.dirname(PROXY_POOL_FILE), { recursive: true });
      fs.writeFileSync(PROXY_POOL_FILE, '[]', 'utf-8');
      return [];
    }
    const raw = fs.readFileSync(PROXY_POOL_FILE, 'utf-8');
    return JSON.parse(raw);
  } catch (err) {
    console.error('Error loading proxy pool:', err);
    return [];
  }
}

export function saveProxyPool(pool) {
  try {
    fs.writeFileSync(PROXY_POOL_FILE, JSON.stringify(pool, null, 2), 'utf-8');
  } catch (err) {
    console.error('Error saving proxy pool:', err);
  }
}

export function importProxiesFromText(text) {
  const pool = loadProxyPool();
  const existingKeys = new Set(pool.map((p) => `${p.host}:${p.port}`));
  const lines = text.split('\n');
  let addedCount = 0;

  for (let line of lines) {
    line = line.trim();
    if (!line || line.startsWith('#')) continue;

    const parts = line.split(':');
    if (parts.length >= 2) {
      const host = parts[0].trim();
      const port = parseInt(parts[1].trim(), 10);
      const username = parts.length >= 4 ? parts[2].trim() : '';
      const password = parts.length >= 4 ? parts[3].trim() : '';

      const key = `${host}:${port}`;
      if (!existingKeys.has(key) && port > 0) {
        existingKeys.add(key);
        pool.push({
          id: `proxy_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
          host,
          port,
          username,
          password,
          type: 'socks5',
          assigned: false,
          profile_id: null,
          latency_ms: null,
          last_checked: null,
        });
        addedCount++;
      }
    }
  }

  saveProxyPool(pool);
  return { added: addedCount, total: pool.length };
}

export function assignProxy(profileId) {
  const pool = loadProxyPool();
  for (const proxy of pool) {
    if (!proxy.assigned) {
      proxy.assigned = true;
      proxy.profile_id = profileId;
      saveProxyPool(pool);
      return proxy;
    }
  }
  return null;
}

export function releaseProxy(profileId) {
  const pool = loadProxyPool();
  let released = false;
  for (const proxy of pool) {
    if (proxy.profile_id === profileId) {
      proxy.assigned = false;
      proxy.profile_id = null;
      released = true;
    }
  }
  if (released) {
    saveProxyPool(pool);
  }
  return released;
}

export function syncProxyAssignment(profileId, host, port) {
  const pool = loadProxyPool();
  let modified = false;

  // Release any existing proxy that was assigned to this profile but no longer matches
  for (const proxy of pool) {
    if (proxy.profile_id === profileId) {
      if (!host || proxy.host !== host || Number(proxy.port) !== Number(port)) {
        proxy.assigned = false;
        proxy.profile_id = null;
        modified = true;
      }
    }
  }

  let matched = null;
  // If new host & port match an item in the pool, claim it for profileId
  if (host && port) {
    for (const proxy of pool) {
      if (proxy.host === host && Number(proxy.port) === Number(port)) {
        if (!proxy.assigned || proxy.profile_id !== profileId) {
          proxy.assigned = true;
          proxy.profile_id = profileId;
          modified = true;
        }
        matched = proxy;
        break;
      }
    }
  }

  if (modified) {
    saveProxyPool(pool);
  }
  return matched;
}

export function deleteProxy(proxyId) {
  const pool = loadProxyPool();
  const filtered = pool.filter((p) => p.id !== proxyId);
  saveProxyPool(filtered);
  return true;
}

export async function fetchProxyGeo(ip) {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3500);
    const res = await fetch(`http://ip-api.com/json/${ip}?fields=status,message,country,countryCode,regionName,city,timezone`, {
      signal: controller.signal,
    });
    clearTimeout(timeout);
    if (res.ok) {
      const data = await res.json();
      if (data.status === 'success') {
        return {
          country: data.country || 'United States',
          country_code: data.countryCode || 'US',
          region: data.regionName || 'California',
          city: data.city || 'Palo Alto',
          timezone: data.timezone || 'America/Los_Angeles',
        };
      }
    }
  } catch (err) {
    console.error(`Failed to fetch geo for ${ip}:`, err.message);
  }
  return {
    country: 'United States',
    country_code: 'US',
    region: 'California',
    city: 'Palo Alto',
    timezone: 'America/Los_Angeles',
  };
}

export async function enrichProxyWithGeo(proxy) {
  if (!proxy.timezone || !proxy.city) {
    const geo = await fetchProxyGeo(proxy.host);
    proxy.country = geo.country;
    proxy.country_code = geo.country_code;
    proxy.region = geo.region;
    proxy.city = geo.city;
    proxy.timezone = geo.timezone;
  }
  return proxy;
}

export function testProxyPing(host, port, timeoutMs = 4000) {
  return new Promise((resolve) => {
    const start = Date.now();
    const socket = new net.Socket();

    socket.setTimeout(timeoutMs);

    socket.connect(port, host, () => {
      const latency = Date.now() - start;
      socket.destroy();
      resolve({ success: true, latency_ms: latency });
    });

    socket.on('error', (err) => {
      socket.destroy();
      resolve({ success: false, latency_ms: null, error: err.message });
    });

    socket.on('timeout', () => {
      socket.destroy();
      resolve({ success: false, latency_ms: null, error: 'Connection timed out' });
    });
  });
}
