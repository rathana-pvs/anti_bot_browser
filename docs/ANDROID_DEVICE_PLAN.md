# Android devices: design and implementation plan

Date: 2026-10-28 · Revision: 1 · Status: Milestone 1 implemented. ADB discovery, users, health, user switching
with readiness and the scrcpy live view checked on a Pixel 10 Pro XL (Android 17).

## Recommendation

Add physical Android phones as a second profile runtime beside Docker browser
containers. A phone is a lockable **device**; each Android user on that phone is
one **profile** with its own Facebook app login and its own proxy. The worker
controls phones over ADB and a small companion app of our own that tunnels each
Android user's traffic through its proxy with a kill switch.

| Choice | Decision and reason |
| --- | --- |
| Device source | Real phones only. |
| Account model | One Facebook account per Android user; many users per phone. |
| Proxy | Own companion app (`VpnService` + tun2socks), one tunnel per Android user. ADB `http_proxy` is device-wide, HTTP-only and has no kill switch. |
| Kill switch | App-level packet drop on proxy failure plus Android Always-on VPN with lockdown. |
| Root | Not required. The root + iptables alternative was considered and not chosen. |
| SIM | None, to avoid carrier-country mismatch. |
| Location | Off, not faked. Mock locations are flagged to apps by `Location.isMock()`. |
| Google account | None in secondary users. Facebook reaches them via `pm install-existing`. |
| Timezone | Group each phone's profiles by proxy timezone. Switching on user change is an opt-in fallback. |
| Facebook login | Manual by the operator, only after the preflight gate passes. |

## Repository findings and limits

- Profiles are modelled in `backend/models/profile.py` (`NetworkIntent`,
  `RequestedEnvironmentInput`). They currently assume a Docker container.
- Tasks in `automation/tasks/` call `automation/engine/container_client.py` and
  `automation/engine/human_input.py` directly (xdotool, X11, GTK file chooser).
  There is no driver interface yet.
- Vision, OCR and screen-state code (`automation/engine/vision.py`,
  `screen_state.py`, `ocr_reader.py`) work on screenshots and should carry over.
- Brains (`automation/brains/<workflow>/bundled_default/workflow.yaml`) use a
  capability vocabulary (`click_candidate`, `select_media`, `type_text`,
  `dismiss_known_dialog`) that is surface-neutral; their states and templates are
  web-specific.
- Admission and resource logic live in `backend/services/resource_service.py`,
  `automation_service.py` and `queue_service.py`. Device admission must go
  through the same path as browser profiles.
- Proxy checks exist in `backend/services/proxy_quality.py` and `proxy_service.py`.
- Worker protocol v1 (`docs/worker/protocol-v1.md`) advertises capabilities in
  `hello`. The sibling `main-server` repository was not available when this plan
  was written, so cloud impact is unverified.

## Data model

**Device** (new): ADB serial, model, Android version, connection (USB/Wi-Fi),
max users, Facebook app version, companion app version, battery, temperature,
free storage, online state, last seen.

**Profile** gains a runtime:

- `browser_container` — today's behavior.
- `android_user` — `device_id`, `android_user_id`, and the Android user serial
  number from `dumpsys user`, so a deleted and recreated user cannot silently
  take over an old profile.

Network keeps `NetworkIntent` (pool/custom proxy) and records three layers, as the
browser runtime does:

- requested: assigned proxy;
- effective: companion app installed, tunnel running, Always-on and lockdown on;
- observed: exit IP measured inside the Android user, DNS/IPv6/UDP handling,
  timezone, location state, last verified time.

The UI never shows a profile as protected unless the observed layer is verified.

## Device discovery and import

"Scan device" on a Devices page:

1. `pm list users` — Android user ID, name, running state.
2. `pm list packages --user N com.facebook.katana` — Facebook installed.
3. `dumpsys account` — Facebook account name if exposed (to be verified); else
   logged-in state from a screenshot.
4. Show a table of users with Facebook state and linked profile. The operator
   chooses which users to import. Nothing is created automatically.

User 0 (Owner) stays the control user: no Facebook account, no lockdown, so a
broken tunnel can never lock the worker out of the phone.

## Locks and scheduling

- **Profile lock**: unchanged, per account.
- **Device lock**: new, per phone. Only the foreground Android user can be seen
  and controlled, so jobs on one phone run one at a time.
- Job sequence: take device lock → `am switch-user N` if needed → wait for
  `am get-current-user` = N and screen unlocked → preflight gate → run.
- Observed on a Pixel 10 Pro XL (Android 17): `am get-current-user` reports the
  new user about 0.5 s after `am switch-user`, but the user is only running
  about 1.8 s later, and the phone then sits with the screen off and the lock
  screen showing. "Ready" therefore means: current user = N, user N running,
  screen awake, and lock screen not showing. Switching back took under 1 s.
- Manual switching implements this: after `am switch-user` the worker polls for
  up to 15 s, wakes the screen (`KEYCODE_WAKEUP`) and dismisses a swipe lock
  screen (`wm dismiss-keyguard`). Screen state comes from `mWakefulness` in
  `dumpsys power`, lock state from `mKeyguardShowing` with window-policy flags as
  a fallback. An unreadable value or a PIN/pattern lock reports "not ready" with
  the reason; it is never treated as ready. One switch at a time per phone.
- The scheduler groups queued jobs by device and current user to reduce switches
  (roughly 5–15 seconds each).
- A phone that is offline or fails health checks rejects the job at admission.
  That is not a failed post, and is never `uncertain` unless a publish click
  happened.

## Driver interface

Extract a surface-neutral driver from the tasks:

```
screenshot · tap · swipe(path) · type_text · key(back/home/enter)
set_clipboard · put_media · open_target(url/intent) · foreground_app · screen_size
```

- `ContainerDriver` wraps today's `ContainerClient`/`HumanInput` without changing
  behavior. This lands first as its own refactor.
- `AndroidDriver` takes `device_serial` and `android_user_id` and passes
  `--user N` to every `am`/`pm`/broadcast call.
  - Screenshots: `adb exec-out screencap -p`.
  - Touch: scrcpy control channel, so `HumanInput`'s curved, timed paths apply.
  - Text: clipboard and paste. `adb input text` breaks on Unicode and emoji.
  - Media: push to the user's storage, run the media scan with `--user N`, then
    open the Facebook composer with a share intent (`am start --user N`), or pick
    the newest gallery item. Remove pushed media after the run. Per-user storage
    paths must be verified per device.
- Optional element lookup with `uiautomator dump` before OCR, behind a per-device
  setting, because it uses Android's UI automation service and may be detectable.

## Brains and templates

- The catalog is keyed by workflow and surface (`facebook_post` + `android`).
- Android brains reuse the capability vocabulary with new states and templates.
- Templates are versioned by Facebook app version. Play Store auto-update is off;
  the Facebook version is recorded per device and upgraded deliberately after
  templates are checked. `install-existing` keeps every user on a phone on the
  same version.
- Plan for multiple templates per state: accounts may see different UI variants.

## Companion app (`android-agent/`)

A Kotlin APK installed in each Android user.

- `VpnService` with an embedded tun2socks engine (candidates:
  `hev-socks5-tunnel`, `xjasonlzy/tun2socks`; licenses to be checked).
- All apps in the user are tunneled; only the app's own upstream socket is
  excluded with `protect()`.
- DNS is captured and resolved through the proxy. IPv6 is routed into the tunnel
  and dropped unless the proxy supports it. UDP goes through SOCKS5 UDP when
  supported, otherwise blocked so Facebook falls back to TCP.
- On proxy failure the tunnel stays up and drops packets.
- Control: listens on device localhost at base port + user ID, reached via
  `adb forward` with a pairing token. Proxy credentials never appear on command
  lines. Commands: `set_config`, `start`, `stop`, `status`.
- `status` reports tunnel state, exit IP measured through the tunnel,
  DNS/UDP/IPv6 handling, `VpnService.isAlwaysOn()`, `isLockdownEnabled()`
  (Android 10+), and last error.
- Credentials are stored in app-private storage encrypted with Android Keystore.
  Removing a profile uninstalls the app for that user and wipes its config.

Backend: `AndroidNetworkService` mirrors `proxy_service`, pushes the profile's
proxy to the app, and records requested/effective/observed state.

## Onboarding an Android user

Automated per user, each step verified:

1. `adb install --user N` the companion app.
2. `appops set --user N <pkg> ACTIVATE_VPN allow` (VPN consent without the dialog).
3. `dumpsys deviceidle whitelist +<pkg>` (battery exemption).
4. `settings put --user N secure always_on_vpn_app <pkg>` and
   `always_on_vpn_lockdown 1`; restart the user if Android needs it to apply.
5. Push the proxy config and start the tunnel.
6. Apply device settings for that user (see below).
7. Verify through the preflight gate; mark the profile network ready or blocked
   with the reason.

If a vendor ROM blocks steps 2 or 4, the app opens that user's VPN settings
screen, the Devices page shows "needs manual setup", and the profile stays
blocked until the companion app reports Always-on and lockdown as on. Facebook
installation (`pm install-existing --user N com.facebook.katana`) and permission
grants are part of this flow.

### Per-user settings

- Location off, Facebook location permission denied, Wi-Fi and Bluetooth
  scanning off.
- Facebook media and notification permissions pre-granted with `pm grant --user N`.
- Fixed font size, display size, theme and portrait lock.
- Keyboard autocorrect and suggestions off.
- No lock screen (or swipe only); automation never enters PINs.
- No Google account.

## Preflight gate

Runs after switching to user N and before Facebook is opened, for every job and
before every manual login. Any failure blocks the job.

1. Companion app tunnel up; Always-on and lockdown on.
2. Exit IP measured inside user N matches the profile's proxy.
3. DNS, IPv6 and UDP handling as configured.
4. Phone timezone matches the proxy timezone (from exit IP geolocation).
5. Location and scanning off.
6. Device health: ADB authorized, screen unlocked, battery, temperature, storage.
7. Do Not Disturb on for the job's duration.

## Timezone

Android has one timezone per phone. Rule: assign proxies so every profile on a
phone shares a timezone (or UTC offset); the Devices page warns when a phone is
mixed. Fallback, opt-in per phone: set the timezone during the user switch, before
the gate. The command differs by Android version and must be verified. Facebook
in background users will see the clock change, which is why this is not the default.

## Manual Facebook login

1. Devices page → profile → "Log in".
2. The worker switches to user N and runs the full preflight gate.
3. Only if it passes, the live view opens and the operator logs in.
4. The app confirms the logged-in state and marks the profile ready.

Checkpoints and 2FA move the profile to "needs review". With no SIM, accounts
need an authenticator app or email for 2FA. Without root the Facebook session
cannot be backed up; a reset phone means logging in again. Sessions and app data
are never uploaded as evidence.

## Live view

scrcpy streamed into manager-app (WebCodecs) replaces noVNC for Android
profiles. Loopback-only, like the current viewer. New UI uses the shared
`Select` and `Checkbox` components.

Implemented with scrcpy-server 3.3.4 (`backend/vendor/scrcpy`, SHA-256 pinned;
3.3.1 crashes on Android 17, and 4.0 changed the stream format). The backend
pushes the server, forwards its socket to a loopback port and bridges H.264
packets and control messages over `WS /api/devices/{serial}/live?user_id=N`.
The endpoint checks the desktop token itself, because HTTP middleware does not
see WebSockets, and accepts only touch, a fixed set of keys, and text up to
300 bytes. Opening the view switches the phone to user N and waits for
readiness first. WebKitGTK 2.52 (Linux Tauri) decodes it with WebCodecs.

## UI/UX

Phones are managed hardware, like proxies; accounts stay the place where
operators work. The device is a detail of the account.

### Navigation

- Add **Devices** to `NavRail.tsx`, between Accounts and Proxies.
- **Accounts** remains the single list of all profiles, browser and Android.

### Accounts page

- Type icon per row (browser / Android) and a "Phone: <name> · User <id>" line.
- New filter chip: Any type / Browser / Android.
- The Network column shows the observed state for Android: "Proxy verified ·
  2 min ago", "Kill switch off", "Exit IP mismatch". Failures count toward the
  existing attention filter.
- New status "Waiting for phone" while the device runs another user's job.

### Account page (Android)

- Live view: scrcpy in a phone-shaped frame with Back, Home and Recent buttons.
- Network tab with three rows — requested (assigned proxy), effective (tunnel,
  Always-on, lockdown), observed (exit IP, DNS, IPv6, UDP, timezone, location
  off). Each row shows pass/fail with a reason and a Re-check button.
- **Log in** runs the preflight gate and shows its checklist live. The live view
  opens only when every item passes; otherwise it stops at the failing item with
  a fix action.
- Activity tab unchanged.

### Devices page

- One card per phone: name, model, Android version, connection, battery,
  temperature, storage, Facebook version, status (Online / Busy with User N /
  Offline / Needs attention).
- Card warnings: mixed timezones, number of accounts sharing the phone, Facebook
  version without matching templates.
- Opening a phone shows its Android users:

  | Android user | Facebook | Account | Network | Profile |
  | --- | --- | --- | --- | --- |
  | 0 Owner | — | control user | — | — |
  | 10 User 1 | Installed, logged in | linked | verified | Profile A |
  | 11 User 2 | Installed, not logged in | — | not set up | Import |

- **Scan device** refreshes the table. Import is per user; nothing is imported
  automatically.
- Empty state "Add phone": connect USB → allow USB debugging on the phone → Scan.

### Onboarding checklist

Opened after Import, one per Android user. Steps run automatically and show
their result:

1. Install companion app
2. Allow VPN
3. Always-on and kill switch
4. Assign proxy (shared `Select` from the pool, showing each proxy's timezone)
5. Apply phone settings (location off, permissions, display)
6. Verify network
7. Log in to Facebook (manual, in the live view)

A failed step shows a plain reason with **Retry** or **Do it manually**, which
opens the right settings screen through the live view. The account is blocked
from jobs until every step passes.

### Queue and Post

- Android queue rows show the phone and states "Waiting for phone" and
  "Switching user".
- A job rejected by the preflight gate reads "Not started: <check> failed",
  visibly different from a failed post.
- `GroupedProfileSelector` shows the type icon; Post is otherwise unchanged.

### Copy and state rules

- Never say "protected" or "safe" unless verified. Use "Verified 2 min ago",
  "Not verified", "Check failed: exit IP is 1.2.3.4, expected 5.6.7.8".
- Every blocked state offers one clear next action.
- Dropdowns and checkboxes use the shared `Select` and `Checkbox` components.

## Fleet operations

- Phones on chargers continuously: prefer models with a charge limit or bypass
  charging; health check blocks on high temperature.
- Screen off between jobs, woken for each job, low brightness (OLED burn-in).
- OS and Facebook auto-update off.
- After reboot, the worker runs `am start-user N` for each user and re-verifies
  before allowing jobs. Android caps background running users; expect start time.
- USB ADB preferred (powered hub). Wireless debugging changes port after reboot.
  One-time RSA authorization per worker machine.
- No SIM: phones use Wi-Fi only; all profile traffic still goes through each
  user's tunnel.

## Risks

- All accounts on one phone share hardware and network; Facebook may link them.
  The Devices page shows how many accounts share a phone.
- An active VPN is visible to any app on the device.
- Vendor ROMs may restrict multi-user, Always-on VPN, or the ADB settings above.
- Automating many accounts is against Facebook's terms; expect checkpoints and
  bans, and surface locked or banned profiles clearly.
- An authorized ADB host has full control of the phones; protect the worker
  machine and phones physically.

## Cloud impact

Expected minimal: the cloud dispatches jobs to accounts; the worker maps account
→ profile → device. If the cloud ever needs to route by surface, `hello` gains a
capability. This needs checking against `main-server` before any protocol change,
following the shared contract-change workflow. Uncertain-publication handling,
attempt IDs, sequence numbers and fencing tokens are unchanged.

## Implementation milestones and exit criteria

### 0. Proof of concept on one phone

One phone, two Android users, minimal companion app and onboarding script.

Exit: each user exits through a different proxy, measured inside the user;
killing a proxy produces no direct traffic; steps 2 and 4 of onboarding work or
the vendor limitation is documented; user switching and timezone commands are
verified for that Android version.

### 1. Device inventory and live view

Device model, ADB discovery, scan and import, Devices page, scrcpy live view,
manual user switching, device health. No automation.

Exit: a phone's users are listed and imported as profiles; live view works per user.

### 2. Companion app and preflight gate

Full companion app, `AndroidNetworkService`, automated onboarding, preflight
gate, manual login flow.

Exit: login view opens only after the gate passes; a failed gate blocks with a
clear reason.

### 3. Driver refactor

`ContainerDriver` extracted from tasks with no browser behavior change.

Exit: existing browser tests and evidence replays pass unchanged.

### 4. `AndroidDriver` and photo posting

Device lock, user switching in admission, share-intent media, caption paste,
publish verification, `uncertain` handling, evidence and telemetry.

Exit: photo posts verified on real runs; an ADB drop during publish yields
`uncertain` with no retry.

### 5. Reel, first comment, warming

Android brains for the remaining workflows, Facebook version pinning and
template sets, scheduler grouping by device.

## Verification

- No CI against real phones. Build an Android screenshot corpus from real runs
  and replay it with `automation/replay_evidence_corpus.py` and
  `replay_recognition_failures.py`.
- A fake `AndroidDriver` for unit tests of admission, locking and the gate.
- Leak tests per release: proxy killed, companion app force-stopped, IPv6 and
  UDP probes, DNS probe.
