# Local worker dialect v1

Status: local worker implemented; cloud supports this dialect through its
`/ws/workers` boundary adapter. This file specifies the local dialect.
`main-server/contracts/worker-protocol.md` describes both cloud dialects; the
cloud native `/ws/worker` dialect is different and must not be used with this agent.
Protocol changes require coordinated implementation.

## Transport and authentication

Workers open an outbound secure WebSocket connection to the configured cloud
endpoint (suggested path `/ws/workers`). The handshake carries
`Authorization: Bearer <worker credential>`. Credentials are not query parameters.
TLS verification stays enabled. No public inbound worker port is needed.

For tests only, the local administrator may enable `allow_loopback_development`
and use `ws://127.0.0.1`. File transfers use HTTPS to exact administrator-approved
storage hostnames, with redirects disabled. WebSocket messages do not carry file
bytes, arbitrary local paths, shell commands, proxy settings, or browser cookies.

JSON messages use this envelope:

```json
{
  "protocol_version": 1,
  "type": "execution_event",
  "message_id": "unique-message-id",
  "reply_to": null,
  "attempt_id": "attempt-123",
  "lease_token": "opaque-attempt-fencing-token",
  "payload": {"sequence": 2, "status": "running", "stage": "composing", "history": []}
}
```

All messages require `protocol_version`, `type`, and `message_id`. Attempt and
fencing fields are required for attempt operations. `reply_to` correlates replies.
Unknown envelope fields, versions, and message types are rejected. Maximum inbound
message size is 256 KiB. Optional fields may be absent or null. This worker version
supports exactly one media asset per execution.

## Connection sequence

1. Worker sends `hello` with `worker_id`, cloud account IDs, and capabilities
   `photo`, `reel`, `png_evidence`, `pause_resume`, `cancel_pending`.
2. Server authenticates the credential against that worker and responds
   `hello_ack`, with `reply_to` identifying the hello message.
3. Worker sends `reconcile` listing local attempt IDs, tokens, execution IDs,
   state, and last sequence. States are `accepted`, `authorized`, `submitting`,
   `submitted`, `terminal`. These are journal states, not publication outcomes.
4. Server compares its persisted records and responds `reconcile_ack` with a
   `leases` array. Each element contains `attempt_id`, `lease_token`, `expires_at`.
5. Only attempts explicitly granted can begin new local execution. An empty
   leases array authorizes no existing pending attempts. Terminal history may be
   present in reconciliation but must never be redispatched.

The server must not replace an existing attempt's fencing token or content to
retry it. A retry uses a new attempt ID. A conflicted/missing attempt needs review,
not an inferred failure. This version reports all retained attempts during
reconciliation; journal compaction/pagination is a later coordinated extension.

## Jobs and durable acceptance

Server sends `job_offer` with attempt identity, fencing token, and payload:

```json
{
  "account_id": "cloud-account-01",
  "task": "photo",
  "caption": "Example caption",
  "first_comment": null,
  "media": [{
    "asset_id": "asset-01",
    "download_url": "https://storage.example.com/private/signed-object",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size_bytes": 12345,
    "extension": ".png"
  }]
}
```

Task is `photo` or `reel`. Photo extensions: `.jpg`, `.jpeg`, `.png`, `.webp`.
Reel extensions: `.mp4`, `.mov`. Media limit: 1 GiB. Caption limit: 20,000 characters;
first comment limit: 8,000. The checksum example is a placeholder, not a valid asset.

Worker validates the local account mapping and intake capacity, persists the
attempt and acceptance in SQLite, and sends `job_accept` with
`local_execution_id`, `reply_to`, and matching attempt/token. Invalid/unavailable
jobs receive `job_reject` with `reason: invalid_or_unavailable_job`. Server
acknowledges rejections with `job_reject_ack` correlated to the rejection ID.

Server persists acceptance and sends `job_start` with `expires_at`, matching
attempt/token, and `reply_to` identifying the acceptance message. This both
acknowledges acceptance and grants execution. Replayed acceptance messages must
also receive a correlated `job_start`; this is safe even for a terminal attempt,
which the worker never reexecutes.

Grant expiry must be timezone-aware ISO 8601 and no more than five minutes ahead
of the worker's current clock. Keep machine clocks synchronized. A grant is cloud
eligibility; actual start still depends on the existing local scheduler and locks.
Downloads are size/checksum-verified before enqueueing. After downloads the worker
checks authorization again. Transfer failures produce a pre-publication failure;
the server can issue a fresh attempt with refreshed asset URLs when appropriate.

## Heartbeats and dispatch eligibility

Worker sends `heartbeat` every 15 seconds by default:

- `worker_id`, UTC `timestamp`, and `paused`.
- `available_slots`: remaining accepted-work intake capacity, not a guarantee of
  immediately available browser resources.
- `local_capacity`: local active/available scheduler counts and configured limits.
- `attempts`: nonterminal attempt IDs, tokens, and journal states.

Server responds `heartbeat_ack` with matching `reply_to` and a `leases` array
renewing authorized attempts. Omitting a lease does not revoke it immediately;
it expires at its previous deadline. Pause new work explicitly when needed.

The worker reconnects if application acknowledgements stop arriving for 45 seconds
by default. WebSocket ping/pong separately checks transport health. Exponential
backoff with jitter applies between connections. Reconciliation is mandatory on
each connection. Disconnection and expired grants block new cloud claims without
interrupting an already-running browser task.

## Events, results, and acknowledgements

Worker sends `execution_event` for changed snapshots. Payload includes sequence,
local status, stage, and up to 100 stage/timestamp history entries. Polling is once
per second while connected; history conveys intermediate recorded stages.

Worker sends `execution_result` for terminal outcomes, additionally carrying a PNG
evidence manifest (`filename`, `size_bytes`, `sha256`), optional verified permalink,
first-comment status, and a bounded error code. No raw logs or local paths are sent.
`uncertain`, `needs_review`, and `failed_after_publish` map to cloud `needs_review`.
Skipped local executions map to `failed_before_publish`. Published outcomes remain
published if evidence upload fails.

Server must save an event/result before acknowledging it:

- `event_ack` acknowledges `execution_event`.
- `result_ack` acknowledges `execution_result`.

Both acknowledgements include `reply_to`, `attempt_id`, and `lease_token` matching
the message being acknowledged. Deduplicate on message ID and on attempt/sequence.
Outbox delivery is at least once, retries unacknowledged messages every five
seconds, and replays them after reconnect. Repeated acknowledgements are harmless;
wrong types or attempt/token mismatches close the connection.

## Commands

Server sends `command` with `payload.action`:

- `pause_new_jobs`: stop intake and new cloud execution claims; running jobs and
  desktop jobs continue.
- `resume_new_jobs`: resume intake/claims subject to valid grants.
- `cancel_attempt`: requires attempt/token; confirms cancellation only before the
  local execution is claimed. Active or terminal work returns `too_late`.

Worker saves command application and its `command_result` in the journal.
Repeated commands with the same ID replay the original result without applying
again. Reusing an ID for different content is rejected. Cancellation commands
and their results carry attempt/token; worker-level pause/resume messages do not.
Server replies `command_result_ack` with matching `reply_to` and attempt fields,
when applicable. Result statuses are `applied`, `cancelled`, or `too_late`.

Application and receipt are combined because the supported actions complete
synchronously at a safe boundary. Separate long-running command acknowledgement
and dynamic configuration revisions are not supported by v1.

## Evidence grants

After a terminal result, server may send `evidence_upload` with the matching
attempt/token and exactly `filename`, `upload_url` in its payload. Only named PNG
screenshots in that execution's profile evidence directory may be uploaded;
symlinks, traversal, profile directories, and raw diagnostic JSON are rejected.
Maximum screenshot size is 20 MiB.

Worker performs HTTPS PUT with `Content-Type: image/png` and `Content-Length`.
Signed grants must permit those headers. On success it durably sends
`evidence_uploaded` with file metadata, attempt/token, and `reply_to` identifying
the grant. Server saves the storage association and sends `evidence_ack` correlated
to the uploaded-message ID, including attempt/token.

The grant queue is transient. The server owns retry/expiry and must reissue fresh
grants if upload confirmation is missing. Repeated PUT to the same object should
be safe. Evidence retries must never create a new publication attempt.

## Recovery boundaries and pending extensions

Local fsynced publish-intent markers precede irreversible cloud publish clicks.
Missing submitted execution records, partial intents, or ambiguous outcomes hold
for review. A fencing token prevents stale state updates; it cannot undo an
external website action. Never redispatch accepted work solely because a lease
expired. Cloud account reassignment requires reconciliation.

The cloud service owns enrollment/credential revocation, access checks,
workspace ownership, durable lease policy, job eligibility, and idempotent event
storage. This worker does not implement those cloud responsibilities. Dynamic
configuration, multi-file posts, remote comment-only retry, automatic media URL
refresh, journal pruning, and distributed gateway routing require future work.

## Optional profile group catalog

`hello` and `heartbeat` now include optional `profile_catalog` display metadata:
`groups` is a list of local group labels (including empty groups), and `accounts`
contains `{account_id, name, group}` for locally assigned cloud accounts. An empty
`group` means Ungrouped. The cloud verifies that every account belongs to this
worker and workspace before updating labels. Settings, profile paths, cookies,
credentials, and browser data are never included. Older v1 peers can omit/ignore
this extension. The canonical example is
`../../../main-server/contracts/local-examples/profile-catalog-hello.json` relative
to this document. Cloud bounds are 200 group names of at most 100 characters and
500 account records; the existing 64 KiB cloud message limit still applies.
