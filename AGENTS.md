# Local worker project instructions

## UI components

Use the shared `manager-app/src/components/ui/Select.tsx` and
`manager-app/src/components/ui/Checkbox.tsx` components for dropdowns and
checkboxes. Do not introduce native `<select>` or `<input type="checkbox">`
controls in application UI.

## Read cross-project context

This repository is the local execution half of a two-project system.
Before architecture, worker integration, queue/recovery, or protocol work, read:

- `../main-server/docs/PROJECT_CONTEXT.md` — shared ownership and integration rules.
- `../main-server/IMPLEMENTATION_PLAN.md` — proposed implementation phases.
- This project's `README.md` and relevant existing backend/automation code.

Resolve sibling paths relative to this repository root. If the cloud repository
is absent, report that limitation and use the available context; do not invent
its implementation. Read applicable instructions in the sibling repository
before editing it.

## Project ownership

- This project owns browser profiles, cookies, sessions, proxies, Docker browser
  lifecycle, automation, evidence generation, and local resource scheduling.
- The Python worker agent (`backend/worker/`) belongs here as a background service. It
  connects outward to the cloud and must operate independently of the desktop
  window.
- The sibling `main-server` project owns remote users, permissions, authoritative
  cloud jobs, worker assignments, leases, remote dashboard, and private file access.
- The local worker is implemented against protocol v1; the cloud gateway is
  available through its `/ws/workers` adapter. Read `docs/worker/README.md` and
  the canonical cloud contract
  before integration. Verify current code before claiming end-to-end availability.

## Integration constraints

- Worker coordination uses secure WebSocket (`wss://`): jobs, commands,
  heartbeats, progress, results, acknowledgements, and reconciliation.
- Media/evidence transfer uses HTTPS. Local execution APIs remain authenticated
  and loopback-only. Do not expose the existing desktop API publicly.
- Cloud and desktop-triggered work must use the same local account/profile lock
  and resource admission controls.
- Preserve uncertain-publication handling. A dropped connection or expired lease
  is not permission to publish again.
- Keep accepted attempts and unacknowledged events durable. Preserve attempt IDs,
  event sequence numbers, and fencing tokens across reconnects.
- Never upload browser sessions/profile directories to cloud storage as evidence.

## Coordinated changes

For protocol or shared state changes, inspect both projects and identify the
impact on each side. Follow the shared context's contract-change workflow.
Respect unrelated working-tree edits. Cross-project context is not permission
to deploy, publish, message another chat, or overwrite another project's work.
