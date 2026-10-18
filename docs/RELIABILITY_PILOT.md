# Reliability pilot and recovery drills

This plan separates offline safety evidence from real publication. No script in
this document starts a browser, publishes content, retries a publication, or
changes cloud assignments.

## Automated recovery drill

Run from the repository root:

```bash
bash scripts/run_reliability_drills.sh
```

The drill injects process boundaries, corrupt queue state, stale idempotency
claims, lost acknowledgements, reconnects, expired leases, ownership races, and
VNC handshake failures. It uses temporary files and simulated transports. The
same drill runs weekly and on demand in CI.

## Pilot gates

The pilot scorecard is available from authenticated local API endpoint
`GET /api/system/reliability` or from:

```bash
automation/venv/bin/python scripts/pilot_scorecard.py
```

The command exits `0` only when every gate passes, `1` for a failed gate, and
`2` when fewer than 30 terminal executions have been observed. Default gates:

- At least 30 terminal executions.
- Uncertain or needs-review outcomes at or below 2%.
- Execution telemetry on at least 95% of terminal attempts.
- Evidence on at least 95% of terminal attempts.
- Zero duplicate-publication signals.
- Zero unresolved outcomes older than 24 hours.

A passing scorecard is evidence, not authorization to deploy or publish.

## Human-controlled canary

Use dedicated canary accounts and harmless, uniquely identifiable media. Obtain
approval for the exact accounts, content, audience, and schedule before starting.
Begin with one profile and one post; do not batch the first attempt.

For each canary:

1. Record the local execution ID and, for cloud work, attempt ID and fencing token.
2. Observe preparation, irreversible-click intent, confirmation, permalink, and evidence.
3. Disconnect coordination once before dispatch and once during a non-publishing stage.
4. Restart the local worker after acceptance but before authorization; verify that acceptance and outbox messages replay without a second execution.
5. Never disconnect deliberately after the irreversible click. If an organic failure occurs there, verify `needs_review` and resolve it manually on Facebook.
6. Confirm that desktop Run Now cannot retry a cloud-owned attempt.
7. Run the scorecard and archive the sanitized support bundle.

Stop the pilot immediately for a duplicate, missing durable intent marker,
unfenced execution, evidence containing profile/session data, or automatic retry
after an ambiguous publication. Preserve the local journal, queue, intent marker,
and evidence for investigation.

## Promotion

Promotion requires the offline drill to pass on the release artifact, every
scorecard gate to pass, all uncertain outcomes to be resolved, and a documented
rollback owner. Production storage and the cloud adapter must be verified
together before remote dispatch is unpaused.
