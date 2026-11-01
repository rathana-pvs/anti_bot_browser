# Network agent (companion app)

See [docs/ANDROID_DEVICE_PLAN.md](../docs/ANDROID_DEVICE_PLAN.md). Installed into each Android user that
holds an account, it sends all of that user's traffic through the account's SOCKS5 proxy and blocks
it when the proxy is unavailable. It also keeps the account's details, so any PC can read the
account back from the phone. The desktop app installs and configures it (`backend/services/android_setup.py`).

## How it works

- `AgentVpnService` opens a VPN (tun) interface routing all IPv4 traffic and DNS
  into [hev-socks5-tunnel](https://github.com/heiher/hev-socks5-tunnel) (MIT), which forwards
  it to the proxy. Only the proxy endpoint is routed outside the tunnel (`excludeRoute`).
- DNS uses the engine's mapped DNS: names are answered with addresses in `100.64.0.0/10` and
  resolved by the proxy, so lookups never use the phone's own network.
- Fail-closed: the tun interface stays up with no proxy, a dead proxy, or a stopped engine,
  so packets are dropped. Android's Always-on VPN with lockdown covers the app being killed.
- `ControlProvider` is the worker's control channel, called with
  `adb shell content call --user N --uri content://com.isolatedbrowser.agent.control --method M`.
  It rejects every caller except ADB shell and root.
- Commands: `status` (also the saved proxy without its password, the account details and whether
  a PIN is set), `set_config`, `check`, `stop`, `set_account`, `same_proxy`, `try_config` (keeps a
  new proxy only if a reply comes back through it), `export` (needs the PIN when one is set),
  `set_pin` and `clear_pin` (need the current PIN or the saved proxy's full details).
- `ControlActivity` starts the service; launched from ADB it may start a foreground service.
- The saved proxy is encrypted with an AES-256-GCM key held in Android Keystore
  (`SecretStore`). The engine gets its config, which includes the password, through a
  named pipe, so it is never written to storage.

## Build

Requires JDK 17 and the Android SDK (platform 35, build-tools 35.0.0, NDK 27.2.12479018).
The engine source must be present at `third_party/hev-socks5-tunnel`, pinned to tag `2.18.0`
with its submodules.

```bash
cd android-agent && ./gradlew assembleDebug
```

### Release build bundled with the app

Releases are signed with one fixed key, kept out of git: every update must use the same key, or
Android refuses to install it over the app already on a phone. Put the key's details in
`android-agent/keystore.properties` (`storeFile`, `storePassword`, `keyAlias`, `keyPassword`) or in
`AGENT_KEYSTORE_FILE`, `AGENT_KEYSTORE_PASSWORD`, `AGENT_KEY_ALIAS` and `AGENT_KEY_PASSWORD`, then:

```bash
android-agent/tools/bundle_release.sh
```

It builds the release APK (not debuggable, so `run-as` cannot read the app's files) and copies it
to `backend/vendor/android-agent/agent.apk` with `manifest.json` (version and SHA-256). The backend
installs only an APK that matches that SHA-256. Commit both files together.

A phone with a debug build installed must have it uninstalled from each user before the release
build can be installed, because the two are signed with different keys.

## Try it on a phone

Setup normally runs from the app. `tools/agent_poc.py` remains for testing: it runs one step at a time (install, always-on, start, set-proxy, status,
check, leak-test, remove). See its `--help`. Proxies are read from the app's proxy pool; set
`AUTOMAT_FB_ROOT` to the app's data folder.
