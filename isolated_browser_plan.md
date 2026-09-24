# Isolated Browser System for Facebook Auto-Posting

## Project Overview

Build a multi-profile isolated browser management system where each profile runs inside a Linux Docker container on a Windows host. Every profile has a unique device fingerprint, dedicated residential proxy, and isolated storage — appearing as a completely separate device to Facebook's detection systems.

**Primary Use Case**: Facebook auto-posting across multiple accounts at scale.

**Host Machine**: Windows, Intel UHD GPU, 64 GB RAM.

---

## Architecture Summary

```
Windows Host
    │
    ├── Manager App (Tauri/Rust desktop app)
    │       ├── Profile list & status dashboard
    │       ├── VNC viewer (one profile at a time)
    │       ├── Proxy configuration per profile
    │       └── Container lifecycle control (start/stop/pause)
    │
    └── Docker Desktop (WSL2 mode — Linux containers)
            ├── Profile 1 Container
            │     ├── Linux (Debian slim)
            │     ├── Google Chrome
            │     ├── Xvfb (virtual display)
            │     ├── TigerVNC server
            │     ├── tun2socks (proxy tunnel)
            │     └── Profile volume (cookies, storage)
            ├── Profile 2 Container (identical structure)
            └── Profile N Container (identical structure)
```

---

## Technology Stack

| Layer | Technology | Why |
| :--- | :--- | :--- |
| Manager App | Tauri (Rust) + React | Native Windows app, low RAM, embeds noVNC |
| Container Runtime | Docker Desktop (WSL2) | Linux containers on Windows host |
| OS inside container | **Ubuntu 22.04 LTS** | Most common Linux desktop, best MS font support, closest to Windows ClearType rendering |
| Browser | Google Chrome (stable) | Highest Facebook trust score |
| Virtual Display | Xvfb | Offscreen rendering, zero real GPU display cost |
| VNC Server | TigerVNC | Lightweight, fast encoding |
| VNC Client | noVNC (embedded in Tauri) | Web-based, easy to embed |
| Proxy Tunnel | tun2socks | Routes 100% traffic through SOCKS5 proxy |
| Storage Isolation | Docker volumes | One volume per profile |
| Fingerprint Config | JSON per profile | Locked hardware identity per profile |
| Automation (Phase 4) | ADB + xdotool + EasyOCR | OS-level control, no CDP |

---

## Phase 1: Core Container (Weeks 1–3)

**Goal**: Single isolated Chrome browser running in a container, visible on Windows desktop via VNC.

### 1.1 Docker Base Image

**File**: `Dockerfile`

```dockerfile
FROM ubuntu:22.04

# Prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/New_York

# System dependencies
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates tzdata \
    xvfb tigervnc-standalone-server \
    tun2socks redsocks iptables iproute2 \
    # Ubuntu font stack (better than Debian default)
    fonts-liberation \
    fonts-noto \
    fonts-ubuntu \
    fonts-dejavu-core \
    fonts-noto-color-emoji \
    fontconfig \
    software-properties-common \
    dbus-x11 x11-utils scrot \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Native Ubuntu font stack (fonts-ubuntu, fonts-liberation, fonts-dejavu, fonts-noto)
# Authentic Linux FreeType font rendering matching Linux identity

# Install Google Chrome stable
RUN wget -q -O /tmp/chrome.deb \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt-get install -y /tmp/chrome.deb && \
    rm /tmp/chrome.deb

# Rebuild font cache with all installed fonts
RUN fc-cache -fv

# Configure font rendering to mimic Windows ClearType
# Ubuntu's defaults are already close, this fine-tunes them
COPY fonts.conf /etc/fonts/local.conf

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5900

ENTRYPOINT ["/entrypoint.sh"]
```

**File**: `entrypoint.sh`

```bash
#!/bin/bash
set -e

# Start virtual display
Xvfb :99 -screen 0 ${SCREEN_RESOLUTION:-1920x1080x24} &
export DISPLAY=:99

# Start VNC server (no password for local use, manager app controls access)
tigervncserver :99 -localhost -noxstartup -SecurityTypes None &

# Start proxy tunnel if proxy is configured
if [ -n "$PROXY_HOST" ]; then
    tun2socks -device tun0 -proxy socks5://$PROXY_USER:$PROXY_PASS@$PROXY_HOST:$PROXY_PORT &
    # Route all traffic through tun0
    ip route add default dev tun0
fi

# Launch Chrome with profile-specific settings
google-chrome \
    --display=:99 \
    --user-data-dir=/data/profile \
    --no-first-run \
    --no-default-browser-check \
    --lang=${LANG:-en-US} \
    --window-size=${WINDOW_SIZE:-1280,720} \
    --disable-blink-features=AutomationControlled \
    --enable-gpu-rasterization \
    --disable-features=UserAgentClientHint \
    --user-agent="${USER_AGENT}" \
    &

wait
```

**File**: `fonts.conf` (ClearType-style rendering)

```xml
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <match target="font">
    <edit name="antialias" mode="assign"><bool>true</bool></edit>
    <edit name="hinting" mode="assign"><bool>true</bool></edit>
    <edit name="hintstyle" mode="assign"><const>hintfull</const></edit>
    <edit name="rgba" mode="assign"><const>rgb</const></edit>
    <edit name="lcdfilter" mode="assign"><const>lcddefault</const></edit>
  </match>
</fontconfig>
```

### 1.2 Profile Configuration Schema

**File**: `profiles/<id>/config.json`

```json
{
  "id": "profile_001",
  "created_at": "2026-09-23T10:00:00Z",
  "name": "Account 1",
  "status": "stopped",

  "fingerprint": {
    "webgl_vendor": "Intel Open Source Technology Center",
    "webgl_renderer": "Mesa DRI Intel(R) UHD Graphics 630 (CML GT2)",
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "screen_resolution": "1920x1080",
    "color_depth": 24,
    "timezone": "America/New_York",
    "language": "en-US",
    "hardware_concurrency": 8,
    "device_memory": 8
  },

  "network": {
    "proxy_type": "socks5",
    "proxy_host": "104.23.x.x",
    "proxy_port": 1080,
    "proxy_user": "username",
    "proxy_pass": "password"
  },

  "container": {
    "id": null,
    "vnc_port": 5901,
    "volume_path": "profiles/profile_001/chrome_data"
  },

  "account": {
    "platform": "facebook",
    "email": "",
    "notes": "",
    "warming_start_date": null,
    "warming_complete": false
  }
}
```

### 1.3 Verification Tasks for Phase 1
- [ ] Build Docker image successfully
- [ ] Launch container, Chrome opens in VNC
- [ ] Connect to VNC from Windows (use RealVNC Viewer or noVNC in browser)
- [ ] Visit `chrome://gpu` → confirm GPU is active, not SwiftShader
- [ ] Visit `https://browserleaks.com/canvas` → record canvas hash
- [ ] Restart container → verify same canvas hash (consistency check)
- [ ] Create 3 profiles → verify each has different canvas hash

---

## Phase 2: Manager App UI (Weeks 4–6)

**Goal**: Desktop GUI to create, launch, view, pause, and delete profiles. VNC viewer embedded. Delivered as a single Windows installer.

### 2.1 Manager App Structure (Tauri + React + Python Sidecars)

**Why Tauri**:
```
✅ Native Windows .exe binary (Rust compiled)
✅ Code protection: business logic compiled to machine code
✅ Cannot be reverse-engineered (no readable source)
✅ Sidecar support: bundles Python automation as compiled .exe
✅ Single installer delivery for customers
✅ Small app binary (~8 MB core, ~200 MB with sidecars + models)
✅ License key system embeddable in Rust (cannot be removed)
```

**Full project structure**:
```
isolated-browser/
│
├── manager-app/                      ← Tauri desktop app
│   ├── src-tauri/
│   │   ├── src/
│   │   │   ├── main.rs              ← Tauri entry point + license check
│   │   │   ├── docker.rs            ← Docker API (start/stop/pause/stats)
│   │   │   ├── profile.rs           ← Profile CRUD operations
│   │   │   ├── fingerprint.rs       ← Fingerprint pool generator
│   │   │   ├── sidecar.rs           ← Spawn Python sidecar processes
│   │   │   └── license.rs           ← License key verification
│   │   ├── binaries/                ← Nuitka-compiled Python sidecars
│   │   │   ├── controller.exe       ← Automation controller
│   │   │   ├── ocr_engine.exe       ← EasyOCR wrapper
│   │   │   ├── ai_generator.exe     ← Gemini caption generator
│   │   │   ├── proxy_manager.exe    ← Proxy import + assignment
│   │   │   └── scraper.exe          ← Web scraper
│   │   ├── models/                  ← Pre-bundled EasyOCR models
│   │   │   ├── craft_mlt_25k.pth
│   │   │   └── english_g2.pth
│   │   └── Cargo.toml
│   │
│   └── src/                         ← React frontend
│       ├── App.tsx
│       ├── components/
│       │   ├── ProfileList.tsx      ← Left panel: profile list
│       │   ├── ProfileViewer.tsx    ← Right panel: noVNC embedded viewer
│       │   ├── ProxyPanel.tsx       ← Proxy pool management + import
│       │   ├── CampaignPanel.tsx    ← Content campaign management
│       │   ├── CreateProfileModal.tsx
│       │   └── ProfileCard.tsx
│       └── pages/
│           ├── Dashboard.tsx
│           ├── Proxies.tsx
│           └── Campaigns.tsx
│
├── automation/                       ← Python automation (compiled to sidecars)
│   ├── controller.py
│   ├── ocr_engine.py
│   └── tasks/
│       └── facebook_post.py
│
├── content/                          ← Content pipeline (compiled to sidecars)
│   ├── scraper.py
│   ├── ai_generator.py
│   └── prepare_campaign.py
│
└── proxy/                            ← Proxy management (compiled to sidecar)
    ├── parser.py
    └── manager.py
```

### 2.2 Sidecar Pattern: Tauri Calls Python Automation

```rust
// src-tauri/src/sidecar.rs

use tauri::api::process::Command;

/// Launch automation for a specific profile
pub fn run_post_task(profile_id: &str, campaign_id: &str) -> Result<String, String> {
    let (mut rx, _child) = Command::new_sidecar("controller")
        .map_err(|e| e.to_string())?
        .args([
            "--profile", profile_id,
            "--campaign", campaign_id,
            "--action", "post"
        ])
        .spawn()
        .map_err(|e| e.to_string())?;

    // Stream output back to UI in real time
    while let Some(event) = rx.blocking_recv() {
        // Forward log lines to React frontend
    }
    Ok("Done".to_string())
}

/// Generate captions for a campaign
pub fn run_caption_generation(campaign_path: &str) -> Result<(), String> {
    Command::new_sidecar("ai_generator")
        .map_err(|e| e.to_string())?
        .args(["--campaign", campaign_path])
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}
```

### 2.3 Code Protection: Compile Python Sidecars with Nuitka

```bash
# Build script: build_sidecars.bat (run before packaging)

pip install nuitka

# Compile each Python sidecar to native Windows .exe
nuitka --onefile --windows-console-mode=disable ^
       --include-data-dir=models=models ^
       automation/controller.py ^
       --output-dir=manager-app/src-tauri/binaries/

nuitka --onefile --windows-console-mode=disable ^
       content/ai_generator.py ^
       --output-dir=manager-app/src-tauri/binaries/

nuitka --onefile --windows-console-mode=disable ^
       proxy/manager.py ^
       --output-dir=manager-app/src-tauri/binaries/

# Result: native .exe files in binaries/
# Protection level: near impossible to reverse engineer ✅
```

### 2.4 License Key System (Rust — Cannot Be Removed)

```rust
// src-tauri/src/license.rs

use reqwest;

pub async fn verify_license(key: &str) -> bool {
    let response = reqwest::get(
        format!("https://yourserver.com/api/verify?key={}", key)
    ).await;

    match response {
        Ok(r) => r.status().is_success(),
        Err(_) => false   // No internet = no access
    }
}

// Called on every app startup — compiled into binary
// Cannot be patched out (Rust binary, no bytecode)
```

### 2.5 Single Installer Delivery

**What customer receives**: `IsolatedBrowserManager-Setup.exe`

```
Installer contents:
  IsolatedBrowser.exe          ← Main Tauri app (~8 MB)
  sidecars/controller.exe      ← Compiled automation (~40 MB)
  sidecars/ai_generator.exe    ← Compiled Gemini client (~20 MB)
  sidecars/proxy_manager.exe   ← Compiled proxy manager (~10 MB)
  models/craft_mlt_25k.pth     ← Pre-bundled OCR model (~85 MB)
  models/english_g2.pth        ← Pre-bundled OCR model (~15 MB)
  ─────────────────────────────────────────────────────
  Total installer size:         ~200–250 MB

Installer auto-handles:
  ✅ Installs all components to Program Files
  ✅ Checks for Docker Desktop → prompts install if missing
  ✅ Pulls Ubuntu Docker image on first run (~1 GB, automatic)
  ✅ Creates desktop shortcut

Customer experience:
  1. Run IsolatedBrowserManager-Setup.exe
  2. Wait 5–10 minutes (Docker + Ubuntu image)
  3. Click desktop shortcut → app opens
  4. Enter license key → done
  5. Never touches Docker, Python, or terminal again
```


### 2.6 UI Theme & Design System (Tailwind Zinc)

**Core Design Philosophy**: Strictly functional, monochrome-first engineering palette. Avoid random or decorative colors; every pixel and tone has a specific functional purpose.

#### 1. Color Palette Tokens
| Role | Token / Hex | Tailwind Class | Purpose |
| :--- | :--- | :--- | :--- |
| **App Background** | `#09090B` | `bg-zinc-950` | Deep neutral base; frames embedded VNC window without glare |
| **Card / Sidebar** | `#18181B` | `bg-zinc-900` | Elevated container surfaces for profile list & control panels |
| **Hover / Active Row**| `#27272A` | `bg-zinc-800` | Interactive item states & selection highlight |
| **Borders** | `#27272A` | `border-zinc-800` | Crisp 1px structural boundaries |
| **Dividers** | `#3F3F46` | `border-zinc-700` | Section dividers |
| **Primary Text** | `#FAFAFA` | `text-zinc-50` | Profile names, primary metrics, active tab labels |
| **Secondary Text** | `#A1A1AA` | `text-zinc-400` | IPs, proxy ports, hardware specs, secondary labels |
| **Muted Text** | `#71717A` | `text-zinc-500` | Inactive status text, timestamps, unit labels |
| **Primary Action** | `#FFFFFF` | `bg-white text-zinc-950` | Primary CTA buttons (Create Profile, Launch All) |
| **Secondary Action** | `#27272A` | `bg-zinc-800 text-zinc-50` | Secondary buttons (Pause, Settings, Inspect) |

#### 2. Strict Semantic State Rules (Status Dots & Text Only)
* **Running / Healthy (`#22C55E` / `text-green-500`)**: Solid 6px dot for active container or live proxy ping.
* **Paused / Warming (`#F59E0B` / `text-amber-500`)**: Solid 6px dot for paused profile or warming alert badge.
* **Stopped / Normal Inactive (`#71717A` / `text-zinc-500`)**: Neutral gray 6px dot. Inactive is normal, NOT an error.
* **Critical Error / Leak (`#EF4444` / `text-red-500`)**: Solid 6px dot / red text. Reserved exclusively for proxy drops, killswitch triggers, or crashed containers.
* **Visual Hierarchy Rule**: NEVER use full-card colored backgrounds (no blue, green, or red cards). The embedded VNC browser viewport must remain the visual center of focus.

#### 3. Technology Stack for UI
* **Styling Engine**: Tailwind CSS
* **Primitive Components**: shadcn/ui (Radix Primitives)
* **Iconography**: Lucide React (clean monochrome stroke icons)

### 2.7 Manager App UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Isolated Browser Manager                    [Settings]  [+ New]│
├─────────────────┬───────────────────────────────────────────────┤
│ PROFILES        │                                               │
│                 │  ┌─────────────────────────────────────────┐  │
│ ● profile_001   │  │                                         │  │
│   Account 1     │  │   [Chrome browser rendered here via     │  │
│   US · Running  │  │    noVNC — fully interactive]           │  │
│   [Pause][Stop] │  │                                         │  │
│                 │  └─────────────────────────────────────────┘  │
│ ○ profile_002   │                                               │
│   Account 2     │  Proxy:   104.23.x.x (US Residential) ●      │
│   UK · Stopped  │  Status:  Running · 2h 14m                   │
│   [Launch]      │  RAM:     423 MB                             │
│                 │  Canvas:  a3f9b2c1 (consistent ✓)            │
│ ○ profile_003   │  Warming: Week 2 of 4                        │
│   Account 3     │                                               │
│   Paused        │  [Open Full Window]  [Pause]  [Stop]         │
│   [Resume]      │                                               │
├─────────────────┴───────────────────────────────────────────────┤
│ Active: 1 · Paused: 1 · Stopped: 1 · RAM: 423 MB / 64 GB      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.8 Core Features to Build
- [ ] Create profile (name, proxy, fingerprint config)
- [ ] Launch profile → `docker run` with correct env vars
- [ ] View profile → embedded noVNC connects to container VNC port
- [ ] Switch profiles → disconnect old VNC, connect new VNC
- [ ] Pause → `docker pause` (0% CPU, RAM stays)
- [ ] Resume → `docker unpause`
- [ ] Stop → `docker stop` (frees RAM, data persists)
- [ ] Delete → `docker rm` + delete profile folder
- [ ] Status bar showing total RAM usage and active count

### 2.9 Fingerprint Pool Generator

When creating a new profile, auto-assign a unique fingerprint from a pool:

```rust
// fingerprint.rs
const GPU_POOL: &[(&str, &str)] = &[
    ("Intel Open Source Technology Center", "Mesa DRI Intel(R) UHD Graphics 630 (CML GT2)"),
    ("Intel Open Source Technology Center", "Mesa Intel(R) UHD Graphics 770 (ADL-S GT1)"),
    ("Intel Open Source Technology Center", "Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2)"),
    ("AMD", "AMD Radeon RX 580 Series (POLARIS10, DRM 3.42.0, LLVM 15.0.7)"),
    ("AMD", "AMD Radeon Graphics (radeonsi, renoir, LLVM 15.0.7)"),
    // Add 30+ entries for large profile counts
];

const SCREEN_RESOLUTIONS: &[&str] = &[
    "1920x1080", "1366x768", "1536x864",
    "2560x1440", "1280x720", "1440x900",
];

fn generate_unique_fingerprint(existing_profiles: &[Profile]) -> Fingerprint {
    // Pick a combination not already used by any existing profile
    // Ensures no two profiles ever share the same fingerprint
}
```

---

## Phase 3: Network Isolation + Proxy (Weeks 7–8)

**Goal**: Each profile routes 100% of traffic through its dedicated proxy. Zero IP leaks.

### 3.1 Network Architecture Per Container

```
Container (Profile 1)
├── eth0 (default Docker bridge) → BLOCKED by iptables rules
├── tun0 (tun2socks virtual interface) → ALL traffic routed here
│       │
│       └── SOCKS5 Proxy → 104.23.x.x:1080 (Residential IP)
│
└── Chrome --proxy-server="socks5://..." (belt-and-suspenders)
```

### 3.2 Proxy File Import System

**Supported file format** (`proxies.txt`):
```
ip:port:username:password

45.58.228.187:5859:wcdxroye:6gcbfuwqjepd
9.142.35.245:6416:wcdxroye:6gcbfuwqjepd
138.226.89.194:7382:wcdxroye:6gcbfuwqjepd
```

**File structure**:
```
proxies/
├── proxy_pool.json       ← Master proxy database (parsed from .txt files)
└── imported/             ← Archive of uploaded .txt files
    └── proxies_2026.txt
```

**Proxy Parser** (`proxy/parser.py`):
```python
def load_proxies_from_file(file_path: str) -> list[dict]:
    """Parse ip:port:username:password format proxy file."""
    proxies = []
    with open(file_path, "r") as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) != 4:
                print(f"Line {line_number}: Invalid format, skipping → '{line}'")
                continue
            ip, port, username, password = parts
            proxies.append({
                "host":       ip.strip(),
                "port":       int(port.strip()),
                "username":   username.strip(),
                "password":   password.strip(),
                "type":       "socks5",
                "assigned":   False
            })
    print(f"Loaded {len(proxies)} proxies from {file_path}")
    return proxies
```

**Proxy Manager** (`proxy/manager.py`):
```python
class ProxyManager:

    def import_from_file(self, file_path: str) -> int:
        """Import proxies from .txt file. Skips duplicates. Returns added count."""
        new_proxies = load_proxies_from_file(file_path)
        existing_hosts = {p["host"] for p in self.proxies}
        added = 0
        for proxy in new_proxies:
            if proxy["host"] not in existing_hosts:
                self.proxies.append(proxy)
                added += 1
        self._save_db()
        return added

    def assign_to_profile(self, profile_id: str) -> dict | None:
        """Auto-assign next available proxy to a profile. One proxy per profile, never shared."""
        for proxy in self.proxies:
            if not proxy["assigned"]:
                proxy["assigned"] = True
                proxy["profile_id"] = profile_id
                self._save_db()
                return proxy
        return None  # No proxies available

    def release_proxy(self, profile_id: str):
        """Return proxy to pool when profile is deleted."""
        for proxy in self.proxies:
            if proxy.get("profile_id") == profile_id:
                proxy["assigned"] = False
                proxy.pop("profile_id", None)
                self._save_db()
                return

    def get_stats(self) -> dict:
        total     = len(self.proxies)
        assigned  = sum(1 for p in self.proxies if p["assigned"])
        return {"total": total, "assigned": assigned, "available": total - assigned}
```

**Manager App Proxy UI**:
```
┌──────────────────────────────────────────────────────┐
│  Proxy Pool                           [Import File]  │
│                                                      │
│  Total: 8  ·  Assigned: 3  ·  Available: 5          │
│                                                      │
│  45.58.228.187:5859    → Profile 1  ● Assigned       │
│  9.142.35.245:6416     → Profile 2  ● Assigned       │
│  138.226.89.194:7382   → Profile 3  ● Assigned       │
│  9.142.34.37:6708      ○ Available                   │
│  192.46.190.165:6758   ○ Available                   │
└──────────────────────────────────────────────────────┘
```

**Auto-assignment flow** — when user clicks `[+ New Profile]`:
```
System checks proxy pool → picks next unassigned proxy →
assigns it to the new profile → saves to profile config JSON →
passes as env vars to Docker container at launch
```

**Proxy passed to container at launch**:
```bash
docker run -d \
  -e PROXY_HOST=45.58.228.187 \
  -e PROXY_PORT=5859 \
  -e PROXY_USER=wcdxroye \
  -e PROXY_PASS=6gcbfuwqjepd \
  ...
```

### 3.3 Proxy Verification Checklist
- [ ] Import `proxies.txt` → all entries parsed correctly, no duplicates
- [ ] Create 3 profiles → each auto-assigned a different proxy
- [ ] Visit `https://browserleaks.com/ip` → shows proxy IP, not host IP
- [ ] Visit `https://browserleaks.com/webrtc` → no local IP leak
- [ ] Visit `https://ipleak.net` → DNS leak test passes
- [ ] Disconnect proxy mid-session → browser loses internet (kill switch working)
- [ ] Delete profile → proxy released back to available pool
- [ ] Verify each profile shows different IP from all others

### 3.4 Chrome Launch Flags for Network

```bash
google-chrome \
    --proxy-server="socks5://${PROXY_HOST}:${PROXY_PORT}" \
    --proxy-bypass-list="<-loopback>" \
    --disable-webrtc-hw-encoding \
    --enforce-webrtc-ip-permission-check \
    --webrtc-ip-handling-policy=disable_non_proxied_udp
```


---

## Phase 4: Automation Layer (Weeks 9–14)

**Goal**: Script Facebook posting actions using OS-level control (xdotool + EasyOCR). No CDP. No `navigator.webdriver`. Parallel execution across multiple profiles.

### 4.1 Why xdotool Not Playwright

```
Playwright requires CDP connection:
  → CDP port open = detectable automation signal
  → navigator.webdriver risk

xdotool controls the real OS mouse/keyboard:
  → No CDP connection at all
  → Chrome has zero awareness of being automated
  → Identical to a human sitting at the keyboard
```

### 4.2 Automation Controller Class

```python
# automation/ocr_engine.py
# Singleton OCR loader — model loads ONCE at app startup, not per screenshot

import easyocr

_reader = None

def get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        print("Loading OCR model (one-time, ~10 seconds)...")
        _reader = easyocr.Reader(['en'], gpu=False)  # Intel UHD has no CUDA
        print("OCR model ready.")
    return _reader
```

```python
# automation/controller.py

import subprocess
import time
import random
import json
import os
import cv2
import numpy as np
from ocr_engine import get_reader

# Directory to store element position caches per profile
CACHE_DIR = "profiles/{profile_id}/element_cache"
# Directory to store template images for common Facebook elements
TEMPLATE_DIR = "automation/templates"

class ProfileController:
    def __init__(self, profile_id: str, display: str, vnc_port: int):
        self.profile_id = profile_id
        self.display = display       # e.g. ":99"
        self.vnc_port = vnc_port
        self.cache_file = f"profiles/{profile_id}/element_cache.json"
        self._position_cache = self._load_cache()

    # ─────────────────────────────────────────────
    # CORE: Hybrid Element Finder
    # Priority: Cache (0ms) → Template Match (10ms) → OCR (2s)
    # ─────────────────────────────────────────────

    def find_element(self, label: str) -> tuple | None:
        """
        Find an element using 3-tier hybrid approach.
        Fastest method tried first, slowest method as final fallback.
        Caches successful results to speed up future calls.
        """
        screenshot_path = self.screenshot()

        # ── Tier 1: Cached position (0ms) ──────────────────────────
        cached = self._position_cache.get(label)
        if cached:
            if self._verify_cache(screenshot_path, cached, label):
                return tuple(cached)        # Cache hit ✅ instant
            else:
                del self._position_cache[label]  # Cache stale, invalidate

        # ── Tier 2: OpenCV Template Match (10–50ms) ─────────────────
        template_path = os.path.join(TEMPLATE_DIR, f"{label}.png")
        if os.path.exists(template_path):
            pos = self._template_match(screenshot_path, template_path)
            if pos:
                self._cache_position(label, pos)
                return pos                  # Template match hit ✅ fast

        # ── Tier 3: EasyOCR Text Search (1.5–3s) ────────────────────
        pos = self._ocr_find(screenshot_path, label)
        if pos:
            self._cache_position(label, pos)
            # Save a template crop for next time (avoids OCR on repeat calls)
            self._save_template_from_position(screenshot_path, pos, label)
            return pos                      # OCR hit ✅ accurate but slower

        return None                         # Element not found ❌

    # ─────────────────────────────────────────────
    # TIER 1: Cache Management
    # ─────────────────────────────────────────────

    def _load_cache(self) -> dict:
        if os.path.exists(self.cache_file):
            with open(self.cache_file) as f:
                return json.load(f)
        return {}

    def _cache_position(self, label: str, pos: tuple):
        self._position_cache[label] = list(pos)
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(self._position_cache, f)

    def _verify_cache(self, screenshot_path: str, pos: list, label: str) -> bool:
        """
        Quick check: is something clickable still at the cached position?
        Uses a small crop + template match to verify the element is still there.
        """
        template_path = os.path.join(TEMPLATE_DIR, f"{label}.png")
        if not os.path.exists(template_path):
            return True   # No template to verify against, trust the cache
        crop_match = self._template_match(screenshot_path, template_path, threshold=0.85)
        return crop_match is not None

    # ─────────────────────────────────────────────
    # TIER 2: OpenCV Template Matching
    # ─────────────────────────────────────────────

    def _template_match(self, screenshot_path: str, template_path: str,
                        threshold: float = 0.80) -> tuple | None:
        """Find element by matching a saved image template. ~10–50ms."""
        screen = cv2.imread(screenshot_path)
        template = cv2.imread(template_path)
        if screen is None or template is None:
            return None

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= threshold:
            h, w = template.shape[:2]
            cx = max_loc[0] + w // 2
            cy = max_loc[1] + h // 2
            return (cx, cy)
        return None

    def _save_template_from_position(self, screenshot_path: str,
                                      pos: tuple, label: str, padding: int = 30):
        """Crop element region from screenshot and save as template for next time."""
        screen = cv2.imread(screenshot_path)
        x, y = pos
        x1, y1 = max(0, x - padding), max(0, y - padding)
        x2, y2 = x + padding, y + padding
        crop = screen[y1:y2, x1:x2]
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(TEMPLATE_DIR, f"{label}.png"), crop)

    # ─────────────────────────────────────────────
    # TIER 3: EasyOCR Text Search
    # ─────────────────────────────────────────────

    def _ocr_find(self, screenshot_path: str, target_text: str,
                  confidence: float = 0.75) -> tuple | None:
        """Find element by reading text on screen. ~1.5–3s on Intel UHD CPU."""
        reader = get_reader()   # Already loaded singleton, no delay
        results = reader.readtext(screenshot_path)
        for (bbox, text, conf) in results:
            if target_text.lower() in text.lower() and conf >= confidence:
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                return (cx, cy)
        return None

    # ─────────────────────────────────────────────
    # HUMAN INTERACTION METHODS
    # ─────────────────────────────────────────────

    def xdo(self, cmd: str):
        """Run xdotool command targeted at this profile's display only."""
        subprocess.run(
            f"DISPLAY={self.display} xdotool {cmd}",
            shell=True, check=True
        )

    def screenshot(self) -> str:
        """Capture this profile's virtual display."""
        path = f"/tmp/screen_{self.profile_id}.png"
        subprocess.run(
            f"DISPLAY={self.display} scrot -o {path}",
            shell=True
        )
        return path

    def human_move(self, x: int, y: int):
        """Move mouse with natural jitter — not a perfect straight line."""
        jitter_x = x + random.randint(-4, 4)
        jitter_y = y + random.randint(-4, 4)
        self.xdo(f"mousemove --clearmodifiers {jitter_x} {jitter_y}")
        time.sleep(0.3 + random.random() * 0.5)

    def human_click(self, x: int, y: int):
        """Click with natural press-hold-release timing."""
        self.human_move(x, y)
        time.sleep(0.05 + random.random() * 0.1)
        self.xdo("mousedown 1")
        time.sleep(0.04 + random.random() * 0.09)
        self.xdo("mouseup 1")

    def find_and_click(self, label: str) -> bool:
        """Combined find + click. Returns True if element was found and clicked."""
        pos = self.find_element(label)
        if pos:
            self.human_click(*pos)
            return True
        return False

    def human_type(self, text: str):
        """Type character by character at realistic human WPM speed."""
        for char in text:
            escaped = char.replace("'", "\\'")
            delay_ms = 80 + random.randint(0, 180)
            self.xdo(f"type --clearmodifiers --delay {delay_ms} '{escaped}'")

    def human_scroll(self, direction: str = "down", amount: int = 3):
        """Scroll feed with random step count and natural pauses."""
        button = "5" if direction == "down" else "4"
        for _ in range(amount):
            self.xdo(f"click {button}")
            time.sleep(0.1 + random.random() * 0.3)

    def wait_for_element(self, label: str, timeout: int = 15) -> bool:
        """Wait until an element appears on screen (page loading, etc.)."""
        start = time.time()
        while time.time() - start < timeout:
            if self.find_element(label):
                return True
            time.sleep(1)
        return False
```

### 4.3 Facebook Posting Task

```python
# automation/tasks/facebook_post.py
import time
import random
from controller import ProfileController

def facebook_post_task(controller: ProfileController, post_content: str) -> bool:
    """
    Post content to Facebook mimicking natural human behavior.
    Full session: browse → engage → post → verify.

    Uses hybrid element finder:
      Session 1:  OCR finds elements (2s each) → saves cache + templates
      Session 2+: Cache/template hits (0–50ms each) → near instant
    """

    # Step 1: Wait for Facebook feed to fully load
    if not controller.wait_for_element("What's on your mind", timeout=20):
        return False
    time.sleep(2 + random.random() * 3)

    # Step 2: Scroll feed to simulate reading (human warmup)
    for _ in range(random.randint(2, 5)):
        controller.human_scroll("down", random.randint(2, 4))
        time.sleep(1.5 + random.random() * 3)

    # Step 3: Randomly like 1–2 posts (natural engagement)
    if random.random() > 0.4:
        controller.find_and_click("Like")
        time.sleep(0.5 + random.random())

    # Step 4: Scroll back to top
    controller.human_scroll("up", random.randint(5, 10))
    time.sleep(1 + random.random() * 2)

    # Step 5: Click post composer
    if not controller.find_and_click("What's on your mind"):
        return False
    time.sleep(1.5 + random.random() * 2)

    # Step 6: Type post at human speed
    controller.human_type(post_content)
    time.sleep(3 + random.random() * 5)   # "reviewing before posting"

    # Step 7: Click Post button
    if not controller.find_and_click("Post"):
        return False
    time.sleep(2 + random.random() * 3)

    # Step 8: Verify post appeared on feed
    return controller.wait_for_element(post_content[:20], timeout=10)
```

### 4.4 Parallel Task Runner

```python
# automation/runner.py
import threading
import time
import random
from controller import ProfileController
from tasks.facebook_post import facebook_post_task

class TaskRunner:
    def __init__(self, profiles: list[dict]):
        self.profiles = profiles
        self.results = {}

    def run_profile(self, profile: dict, content: str):
        """Run posting task for one profile"""
        controller = ProfileController(
            profile_id=profile["id"],
            display=profile["display"],
            vnc_port=profile["vnc_port"]
        )
        try:
            success = facebook_post_task(controller, content)
            self.results[profile["id"]] = "success" if success else "failed"
        except Exception as e:
            self.results[profile["id"]] = f"error: {e}"

    def run_parallel(self, content_map: dict[str, str], max_concurrent: int = 5):
        """
        Run tasks across profiles in staggered batches.
        content_map: {profile_id: unique_post_content}
        """
        threads = []

        for i, profile in enumerate(self.profiles):
            content = content_map.get(profile["id"], "")
            t = threading.Thread(
                target=self.run_profile,
                args=(profile, content)
            )
            threads.append(t)
            t.start()

            # Stagger starts: 30–120 seconds between profiles
            if (i + 1) % max_concurrent == 0:
                # Wait for current batch before starting next
                time.sleep(30 + random.random() * 90)
            else:
                time.sleep(5 + random.random() * 25)

        for t in threads:
            t.join()

        return self.results
```

### 4.5 Content Pipeline (Gemini AI + Web Scraper)

**Your input**: Media files + website link
**System output**: Unique hook caption per profile, link posted as first comment

**File structure**:
```
content/
├── campaigns/
│   └── campaign_001/
│       ├── campaign.json                   ← Campaign settings
│       ├── media/
│       │   ├── reel_001.mp4                ← Your videos/images
│       │   └── image_001.jpg
│       └── captions/
│           └── generated/
│               ├── profile_001.txt         ← Auto-generated unique caption
│               ├── profile_002.txt
│               └── profile_003.txt
├── cache/
│   └── scraped_pages.json                  ← Page content cache (scrape once)
└── templates/
    └── automation/                         ← OpenCV button templates
```

**Campaign Config** (`campaign.json`) — the only file you fill in:
```json
{
  "campaign_id": "campaign_001",
  "name": "Product Launch Week 1",
  "media": ["media/reel_001.mp4", "media/image_001.jpg"],
  "media_rotation": "random",
  "comment_link": "https://yourwebsite.com/landing",
  "post_schedule": {
    "time_window_start": "08:00",
    "time_window_end":   "12:00"
  },
  "assigned_profiles": ["profile_001", "profile_002", "profile_003"]
}
```

**Step 1 — Web Scraper** (`content/scraper.py`):
```python
import requests
from bs4 import BeautifulSoup

def scrape_page_content(url: str) -> dict:
    """Scrape key content from landing page for caption generation."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    return {
        "url":              url,
        "title":            soup.find("title").get_text(strip=True)
                            if soup.find("title") else "",
        "meta_description": soup.find("meta", {"name": "description"})["content"]
                            if soup.find("meta", {"name": "description"}) else "",
        "og_description":   soup.find("meta", {"property": "og:description"})["content"]
                            if soup.find("meta", {"property": "og:description"}) else "",
        "h1":               soup.find("h1").get_text(strip=True)
                            if soup.find("h1") else "",
        "h2s":              [h.get_text(strip=True) for h in soup.find_all("h2")][:5],
        "first_paragraph":  soup.find("p").get_text(strip=True)[:300]
                            if soup.find("p") else "",
    }
```

**Step 2 — Gemini Caption Generator** (`content/ai_generator.py`):
```python
import google.generativeai as genai
import json, os, time
from scraper import scrape_page_content

# Free API key: https://aistudio.google.com/app/apikey
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")  # Free tier: 1500 req/day

CACHE_FILE = "content/cache/scraped_pages.json"

def get_page_content(url: str) -> dict:
    """Return cached page content or scrape fresh."""
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            cache = json.load(f)
    if url not in cache:
        print(f"Scraping: {url}")
        cache[url] = scrape_page_content(url)
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    return cache[url]

# Different writing style per profile — deterministic and consistent
STYLES = [
    "Write from personal experience. Sound like someone who used this.",
    "Write as if you just discovered this and are excited to share.",
    "Write a curiosity-gap hook. Tease without revealing everything.",
    "Address a pain point directly. Speak to the struggle first.",
    "Write a bold statement that sounds surprising but is true.",
    "Write as a story opener. First sentence feels like a story beginning.",
    "Write as urgent advice to a close friend.",
    "Write as someone who was skeptical but is now converted.",
]

def generate_caption_from_link(url: str, profile_id: str) -> str:
    """Read page content from URL and generate unique hook caption via Gemini."""
    page = get_page_content(url)

    page_context = f"""
Page title:    {page['title']}
Description:   {page['meta_description'] or page['og_description']}
Main headline: {page['h1']}
Key benefits:  {', '.join(page['h2s'])}
Opening text:  {page['first_paragraph']}
    """.strip()

    # Same profile always gets same style (consistent per account)
    style = STYLES[hash(profile_id) % len(STYLES)]

    prompt = f"""You are a Facebook content creator writing a post caption.

Page content:
{page_context}

Writing style: {style}

Write a Facebook caption that:
- Is 3-5 sentences maximum
- Has a strong scroll-stopping first line
- Does NOT mention the URL or link (link goes in comment)
- Does NOT sound like an advertisement
- Uses 1-2 emojis naturally
- Ends with a soft engagement prompt
- Sounds like a real person sharing something valuable

Write ONLY the caption. No explanation."""

    response = model.generate_content(prompt)
    return response.text.strip()
```

**Step 3 — Batch Caption Preparation** (`content/prepare_campaign.py`):
```python
from ai_generator import generate_caption_from_link
import json, os, time

def prepare_campaign(campaign_path: str):
    """Generate unique captions for all profiles in a campaign. Run once before scheduling."""
    with open(f"{campaign_path}/campaign.json") as f:
        campaign = json.load(f)

    url      = campaign["comment_link"]
    profiles = campaign["assigned_profiles"]

    print(f"Generating captions via Gemini (free tier)...")
    print(f"Link: {url}")
    print(f"Profiles: {len(profiles)}\n")

    os.makedirs(f"{campaign_path}/captions/generated", exist_ok=True)

    for i, profile_id in enumerate(profiles):
        output_file = f"{campaign_path}/captions/generated/{profile_id}.txt"

        if os.path.exists(output_file):
            print(f"  [{i+1}/{len(profiles)}] {profile_id}: Already exists ✓")
            continue

        print(f"  [{i+1}/{len(profiles)}] {profile_id}: Generating...", end=" ", flush=True)
        caption = generate_caption_from_link(url, profile_id)

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(caption)

        print(f"Done ✅  → {caption[:60]}...")

        # Respect free tier rate limit: 15 RPM = 1 request per 4 seconds
        if i < len(profiles) - 1:
            time.sleep(4)

    print(f"\n✅ All {len(profiles)} captions ready.")
```

**Step 4 — Updated Facebook Post Task with Link Comment**:
```python
def post_with_link_comment(controller, caption: str,
                            media_path: str, link: str) -> bool:
    """Post media + caption, then immediately post link as first comment."""

    # Post the content (no link in caption)
    success = upload_post(controller, caption, media_path)
    if not success:
        return False

    # Wait for post to appear
    time.sleep(3 + random.random() * 4)

    # Post link as first comment
    controller.find_and_click("Comment")
    time.sleep(1 + random.random())
    controller.human_type(f"🔗 Full details here → {link}")
    time.sleep(1 + random.random() * 2)
    controller.xdo("key Return")
    time.sleep(2)

    return True
```

**Dependencies** (`requirements.txt`):
```
google-generativeai
requests
beautifulsoup4
easyocr
opencv-python
```

**Setup**:
```bash
pip install -r requirements.txt

# Set free Gemini API key (get from https://aistudio.google.com/app/apikey)
set GEMINI_API_KEY=your-key-here   # Windows
```

**Cost**:
```
Web scraping:        Free (BeautifulSoup)
Caption generation:  Free (Gemini 1.5 Flash free tier — 1,500 req/day)
40 profiles/day:     $0.00 ✅
```

**Your Daily Workflow**:
```
1. Drop media into campaigns/campaign_001/media/
2. Set comment_link in campaign.json
3. Run: python content/prepare_campaign.py
   → System scrapes your link once (cached forever)
   → Generates unique caption per profile via Gemini
   → Saves to captions/generated/
4. Click [Start Campaign] in manager app
   → Posts at random time in your schedule window
   → Link posted as first comment automatically
```


---

## Resource Management & Limits

### Recommended Profile Limits (Intel UHD + 64 GB RAM)

| Mode | Simultaneous Active | Total Stored | GPU Load |
| :--- | :--- | :--- | :--- |
| Conservative | 5–8 | 25 | ~60% |
| Normal | 8–12 | 30 | ~80% |
| Maximum | 12–15 | 35 | ~95% |
| Avoid | 15+ | — | Overloaded |

### Docker Resource Limits Per Container

```bash
docker run \
  --memory="700m" \
  --memory-swap="700m" \
  --cpus="0.8" \
  ...
```

Prevents any single profile from consuming too much RAM or CPU.

### Profile Lifecycle Commands

```bash
# Start a profile
docker run -d --name profile_001 [options] isolated-chrome:latest

# Pause (0% CPU, RAM stays, instant resume)
docker pause profile_001

# Resume
docker unpause profile_001

# Stop (frees RAM, data persists on volume)
docker stop profile_001

# Delete (removes container, keeps data volume)
docker rm profile_001

# Delete everything including data
docker rm profile_001 && rm -rf profiles/profile_001/chrome_data
```

---

## Account Warming Strategy

> [!IMPORTANT]
> Warming is the most critical success factor. No automation before warming is complete.

### Warming Schedule (Per Account)

| Week | Activities | Automation Allowed? |
| :--- | :--- | :--- |
| Week 1 | Manual browsing only. Like posts. Watch videos. | ❌ No |
| Week 2 | Add 5–10 friends. Join 2–3 groups. Comment occasionally. | ❌ No |
| Week 3 | Post 1–2 times manually. React to stories. | ⚠️ Manual only |
| Week 4 | Light automation. 1 auto-post per day max. | ✅ Limited |
| Week 5+ | Gradual increase. Max 2–3 posts per day. | ✅ Full |

### Warming Tracking in Manager App

Each profile config tracks:
```json
"account": {
    "warming_start_date": "2026-09-23",
    "warming_complete": false,
    "warming_week": 2,
    "posts_today": 0,
    "last_post_date": null
}
```

Manager app shows warming status and **blocks automation until warming is complete**.

---

## Proxy Requirements

| Requirement | Specification |
| :--- | :--- |
| Type | Residential SOCKS5 (not datacenter) |
| Session | Sticky (same IP per account every session) |
| Assignment | 1 unique IP per profile, never shared |
| Location | Match account's declared location |
| Recommended providers | Brightdata, Smartproxy, Oxylabs |

---

## Verification Plan

### Phase 1 Verification
- Visit `chrome://gpu` → Hardware acceleration active, no SwiftShader
- Visit `https://browserleaks.com/canvas` → record hash, verify consistent across restarts
- Create 3 profiles → verify each has different canvas hash

### Phase 2 Verification
- Create 10 profiles from UI → no crashes, all configs saved correctly
- Launch 5 simultaneously → VNC viewer switches between them cleanly
- Pause/resume → profile resumes exactly where it left off
- RAM monitor shows expected usage per active profile

### Phase 3 Verification
- Each profile shows different IP on `https://browserleaks.com/ip`
- WebRTC test shows no local IP leak
- DNS leak test passes for all profiles
- Disconnect proxy → browser loses internet immediately (kill switch)

### Phase 4 Verification
- Run automation script on a test site
- Verify `navigator.webdriver` is `false` inside container
- Post successfully on a test Facebook account
- Run 3 profiles in parallel → all post without interfering

---

## Project Timeline

| Phase | Deliverable | Duration | Cumulative |
| :--- | :--- | :--- | :--- |
| Phase 1 | Core container + VNC access | Weeks 1–3 | 3 weeks |
| Phase 2 | Manager app with profile management UI | Weeks 4–6 | 6 weeks |
| Phase 3 | Network isolation + proxy per profile | Weeks 7–8 | 8 weeks |
| Phase 4 | Automation (xdotool + OCR + parallel runner) | Weeks 9–14 | 14 weeks |
| Polish | Bug fixes, UX improvements, content variation system | Weeks 15–16 | 16 weeks |

**Total estimated time**: ~4 months to fully functional product.

---

## Account Authentication & Manual Login Workflow

> [!TIP]
> **Manual Login is the safest authentication tier**:
> Automated login scripts (typing password, dealing with 2FA or captchas) trigger high suspicion. Having the user log in manually once per profile through the embedded VNC viewer provides the highest trust score Facebook recognizes.

### One-Time Manual Login Steps:
1. **Launch container** for Profile from the Manager UI.
2. **Open Profile in VNC viewer** (embedded in Tauri).
3. **Navigate to `facebook.com`** and enter credentials manually (including 2FA if enabled).
4. **Browse naturally** for 5–10 minutes (view feed, like a post).
5. **Session persistence**: All cookies, session tokens, indexedDB, and localStorage are saved inside the profile's dedicated Docker volume (`/data/profile` mapped to `profiles/<id>/chrome_data`).
6. **Subsequent launches**: When the container restarts or unpauses, Chrome automatically uses the persistent volume, remaining logged in without re-entering credentials.

---

## Confirmed Project Decisions & Status

| Category | Decision | Details |
| :--- | :--- | :--- |
| **Desktop Shell** | **Tauri (Rust)** | Native compilation, tamper-proof license check, low resource footprint |
| **Code Protection** | **Nuitka Compiler** | Compiles Python sidecars (`controller.exe`, `ai_generator.exe`, `proxy_manager.exe`) into native binaries |
| **App Delivery** | **Single Installer** | All binaries, EasyOCR models, and front-end packaged into one Windows installer |
| **Container OS** | **Ubuntu 22.04 LTS** | High canvas trust, complete MS core fonts & ClearType fontconfig tuning |
| **Browser Engine** | **Google Chrome (Stable)** | Highest Facebook trust tier |
| **Display & GPU** | **Xvfb + TigerVNC** | Selective one-at-a-time display output; background profiles consume ~0% display GPU |
| **Network & Proxies**| **SOCKS5 via tun2socks** | Imported from `.txt` (`ip:port:user:pass`); fail-closed killswitch |
| **Element Finding** | **3-Tier Hybrid** | Cache (0ms) → OpenCV template match (10ms) → EasyOCR (2s fallback) |
| **Content Pipeline** | **Web Scraper + Gemini** | Scrapes link → Gemini 1.5 Flash generates unique hook captions → link in 1st comment |
| **UI Design System** | **Monochrome Zinc** | Strictly functional, zero random colors; Tailwind Zinc 950/900/800 base, pure white CTAs, semantic status dots (Running, Paused, Inactive gray, Error red) |
| **Fingerprint Strategy**| **Authentic Linux Identity** | 100% consistent Ubuntu 22.04 + Chrome footprint; zero OS spoofing contradictions, zero prototype tampering |
| **Account Strategy** | **Manual Login + Volume** | One-time manual login via VNC; cookies persisted indefinitely in Docker volume |
| **Hardware Baseline**| **Intel UHD + 64 GB RAM** | Run 5–8 active simultaneous profiles smoothly; store 30–35 profiles |
