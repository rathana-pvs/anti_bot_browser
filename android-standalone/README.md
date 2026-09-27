# Standalone ReDroid (Android) + Web Scrcpy Prototype

This directory contains a standalone testbed for running **ReDroid (Android 14 / 15)** inside Docker, paired with **ws-scrcpy** for real-time (60 FPS, <35ms latency) browser viewing and touch control.

---

## 🚀 Quick Start Guide

### Step 1: One-time Host Setup (Kernel Module)
Android's IPC driver (`binder_linux`) must be active on your host Linux system. Run:

```bash
cd android-standalone
sudo ./setup_host.sh
```

*(This loads `binder_linux` with parameters `devices="binder,hwbinder,vndbinder"` and verifies `/dev/dri` GPU access).*

To make this permanent across host reboots:
```bash
echo 'binder_linux' | sudo tee -a /etc/modules
echo 'options binder_linux devices="binder,hwbinder,vndbinder"' | sudo tee /etc/modprobe.d/binder.conf
```

---

### Step 2: Start the Android Environment

```bash
./start.sh
```

This will:
1. Pull the official `redroid/redroid:14.0.0-latest` image.
2. Launch the `redroid-standalone` container with GPU acceleration and standard mobile screen dimensions (720x1280 @ 320 DPI).
3. Launch `ws-scrcpy` on port `8000`.
4. Wait for `sys.boot_completed=1` and link ADB automatically.

---

### Step 3: View & Control in Your Browser

Open Google Chrome or any modern browser:
👉 **[http://localhost:8000](http://localhost:8000)**

1. You will see the connected device listed.
2. Click on the device to open the high-fps canvas stream.
3. You can click, swipe, scroll, and type directly from your desktop.

---

## 🛠️ Handy Commands

| Action | Command |
| :--- | :--- |
| **Stop Containers** | `./stop.sh` |
| **Install an APK** | `./install_apk.sh /path/to/app.apk` |
| **Host ADB Shell** | `adb -s localhost:5555 shell` |
| **Android Logs** | `docker logs -f redroid-standalone` |
| **Switch to Android 15** | `REDROID_IMAGE=redroid/redroid:15.0.0-latest ./start.sh` |

---

## 📐 Customizing Display & Performance

You can customize screen dimensions or GPU behavior directly in `docker-compose.yml`:
* `androidboot.redroid_width=1080`
* `androidboot.redroid_height=1920`
* `androidboot.redroid_dpi=420`
* `androidboot.redroid_gpu_mode=auto` *(tries host Intel GPU `/dev/dri`, falls back to swiftshader)*

---

## 🔮 Next Step: Manager App Integration

Once you test and verify this standalone environment:
1. We can create an `AndroidProfile` type alongside your current `LinuxProfile` in `manager-app`.
2. Embed the `ws-scrcpy` canvas stream into the React profile viewer (either via `<iframe>` or native `@yume-chan/scrcpy` WebCodecs component).
3. Integrate ADB automation commands (`adb shell input ...`) into the Python visual engine.
