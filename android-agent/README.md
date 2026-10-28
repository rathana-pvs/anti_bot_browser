# Network agent (companion app)

Milestone 0 proof of concept from [docs/ANDROID_DEVICE_PLAN.md](../docs/ANDROID_DEVICE_PLAN.md).
Installed into each Android user that holds an account, it sends all of that user's
traffic through the account's SOCKS5 proxy and blocks it when the proxy is unavailable.

## How it works

- `AgentVpnService` opens a VPN (tun) interface routing all IPv4 and IPv6 traffic and DNS
  into [hev-socks5-tunnel](https://github.com/heiher/hev-socks5-tunnel) (MIT), which forwards
  it to the proxy. Only the proxy endpoint is routed outside the tunnel (`excludeRoute`).
- DNS uses the engine's mapped DNS: names are answered with addresses in `100.64.0.0/10` and
  resolved by the proxy, so lookups never use the phone's own network.
- Fail-closed: the tun interface stays up with no proxy, a dead proxy, or a stopped engine,
  so packets are dropped. Android's Always-on VPN with lockdown covers the app being killed.
- `ControlProvider` is the worker's control channel, called with
  `adb shell content call --user N --uri content://com.isolatedbrowser.agent.control --method M`.
  It rejects every caller except ADB shell and root.
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

## Try it on a phone

`tools/agent_poc.py` runs one step at a time (install, always-on, start, set-proxy, status,
check, leak-test, remove). See its `--help`. Proxies are read from the app's proxy pool; set
`AUTOMAT_FB_ROOT` to the app's data folder.
