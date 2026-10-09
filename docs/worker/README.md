# Local cloud worker

The worker belongs to this application. It runs in the same process as the
existing local backend, sharing the scheduler, account locks, containers, and
execution recovery. It connects outward to the cloud over **secure WebSocket
(`wss://`)**. Media and screenshots use separate **HTTPS file transfers**.

The cloud project now provides a `/ws/workers` adapter for this local dialect.
Use that plural endpoint, not its native `/ws/worker` endpoint. The actual agent has passed the cloud gateway integration test with simulated
execution; real browser publishing is not part of those tests.

## Run independently of the desktop window

From the application root:

```bash
automation/venv/bin/python -m pip install -r backend/requirements.txt
cp docs/worker/config.example.json data/worker.json
chmod 600 data/worker.json
# Edit server_url, worker_id, account_profiles, and transfer_hosts.
# Provide the server-issued credential through WORKER_CREDENTIAL or the protected config.
automation/venv/bin/python -m backend.worker --config data/worker.json
```

Never put credentials in URLs or command-line arguments. `WORKER_CREDENTIAL`
overrides the config's `credential` field. Only accounts explicitly mapped by
the local administrator can execute; missing or automation-disabled profiles are
rejected. `transfer_hosts` must list exact trusted object-storage hostnames;
redirects are not followed. Do not configure arbitrary internal hosts.

This command hosts both the existing backend and the worker. It binds a random
loopback port with fresh local API authentication, and writes a mode-0600
`data/worker-service-session.json` descriptor for the desktop to discover. The
cloud credential is never included in that descriptor. A backend lock prevents
two processes from scheduling against the same data directory.

Start the worker service before opening the desktop. The updated Tauri desktop
attaches to a running service found in its runtime or current project directory
and leaves it running when the window closes. For a service in another runtime,
set `WORKER_SERVICE_SESSION_FILE` to its absolute descriptor path before launching
the desktop. The service and UI must use the same application data root.

Desktop Install/Repair and recovery actions refuse to restart an independently
managed service. Stop or restart it through its service manager. If a standalone
backend already owns the data root, the worker refuses startup rather than
running another scheduler. If the service exits while the desktop is attached,
restart the service and reopen the desktop to discover the new port/token.

`deploy/worker.service.example` provides an optional Linux systemd user-service
unit. Replace its paths and create a protected environment file containing
`WORKER_CREDENTIAL=...` before installing it. The example is not installed or
activated automatically. Continuing across logout requires the user's systemd
session policy. Windows/WSL startup packaging is not provided in this first
version; run the Python service inside WSL, and explicitly expose its protected
session descriptor to the Windows desktop if attaching from Windows.

## Local state and recovery

- `data/worker/journal.sqlite3`: accepted attempts, cloud-to-local execution IDs,
  pause state, command results, and unacknowledged outgoing messages.
- `data/worker/publish-intents/`: fsynced, single-use intent markers written
  before an irreversible cloud publish click.
- `profiles/shared_media/worker/`: checksum-verified downloaded media.
- Existing `data/posting_queue.json`: normal local execution scheduling and outcomes.
- Existing profile evidence directories: screenshots stay local until the server
  explicitly supplies an authorized upload grant for a named PNG screenshot.

These runtime paths are ignored by Git. The journal contains temporary signed
file URLs and should be treated as private. No retention/pruning job is installed
yet; monitor disk usage and retain unresolved attempts/intents during cleanup.

A `job_accept` is persisted before it is sent. Execution requires `job_start` or
an explicit reconciliation grant with the same attempt/token. Reconnects block
new dispatch until the server completes reconciliation. Leases are renewed in
application heartbeat acknowledgements; expired leases block queued work, while
already-running work finishes locally and reports after reconnect.

Snapshots are checked every second while connected. Reports include current
status, stage, and a bounded history, so transitions between snapshots can still
appear in the timeline. Results and events are retained until individually
acknowledged. Missing submitted execution records or durable publish intent
around a failed run require review; they are never automatically republished.

Pause/resume affects only new cloud dispatch; local desktop jobs remain available.
Cancellation succeeds only before a local execution is claimed. Active publishing
returns `too_late` rather than claiming that it was stopped. Desktop Run Now
cannot retry a cloud-owned attempt: cloud retries need a new attempt identity.

## Current scope

Supported: one photo or one reel per job, optional first comment on the original
post, secure WebSocket connection, application heartbeat, durable acceptance and
reporting, reconnect reconciliation, pause/resume, safe pending cancellation,
HTTPS media download, bounded PNG evidence upload, and authenticated local status
at `GET /api/worker/status`.

Not yet implemented in this agent: an enrollment client, dynamic configuration/account
assignment updates, multiple media per execution, comment-only remote retry,
automatic signed-URL refresh, automated retention, and production deployment.
Unknown message types/versions fail closed. Settings changes require a controlled
service restart and reconciliation. The journal is bound to its worker ID and
server endpoint; unresolved accounts cannot be remapped to another local profile.
Credentials must be issued by the cloud server; the worker does not create remote users or enroll itself silently.

The canonical wire contract is
`../main-server/contracts/worker-protocol.md` relative to the project root.
The local dialect is documented in `docs/worker/protocol-v1.md`; the cloud contract
documents both dialects and must preserve adapter compatibility when changed.
The cloud adapter now issues durable `evidence_upload` grants and validates uploaded
PNG evidence; actual client transfers passed against local emulated storage. The
selected production storage provider still needs verification before a pilot.

## Validation

```bash
automation/venv/bin/python -m pytest backend/tests/test_worker_agent.py backend/tests/test_worker_integration.py
```

WebSocket integration tests use temporary loopback servers; queue tests use
isolated temporary files. They do not launch browsers or publish real posts.

## Connected local development instance — 2026-10-06

The running desktop backend is connected to the sibling server's demo client over
its loopback `/ws/workers` endpoint, with six existing profiles mapped. Cloud
dispatch stays paused until private media storage and transfer-host configuration
are ready. Connection/heartbeat/reconciliation are verified; no real post was
submitted for this check. See `../../../main-server/docs/LOCAL_APP_CONNECTION.md`
relative to this document for this machine's protected config and service paths.

When source code and persistent runtime data live in different directories, the
backend now selects the automation runner from its own code bundle. This avoids
using an older runtime runner that lacks the current cloud publication guard;
profiles, media, and Brain assets continue using the persistent data root.

Profile display names and groups are sent on connection and every heartbeat for
assigned accounts. The cloud mirrors them in collapsible account groups and the
post picker; this includes empty groups and Ungrouped accounts. Browser settings
and sessions remain local. Group/name changes do not require a service restart;
changing account-to-profile assignment still does.
