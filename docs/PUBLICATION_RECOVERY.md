# Recovering completed local publications

The local manager checks durable publication results before classifying a dead
publisher as interrupted. A completed run can outlive its stdout consumer or
finish just before the manager restarts. The queue's last stage is not the final
outcome in that case.

Recovery requires a `published` result with no error, a valid completion time,
and an exact match between `attempt.json` and the queue's execution ID,
publisher lease ID, and container owner token. The evidence directory must be
inside that profile's evidence root and match its task type; `run_id` must match
the directory. A newer claim supersedes older evidence. A directory hint alone,
nearby timestamp, screenshot, or successful comment is not sufficient proof.

The same publication field mapping is used for ordinary completion and recovery.
Verified comment status, result telemetry, and permalink metadata are restored
without browser actions. Recovery appends an audit history entry and retains
prior history and lease identity. Repeating recovery makes no further changes.

Previously misclassified entries can also be repaired when their last transition
was `manager_restart_recovery`. Manual reviews, cancellation requests, active
attempts, and newer comment retry outcomes are preserved. Missing, corrupt,
ambiguous, or mismatched results continue through the existing uncertainty rules.
No publication or comment is automatically repeated by this reconciliation.

The runner writes `result.json` through a temporary file, flushes and fsyncs it,
then atomically replaces the old snapshot. Incomplete writes cannot replace a
valid previous result. Failed writes remove their temporary files.

This is a local desktop queue repair. Cloud-owned attempts are excluded; cloud
reconciliation authority and protocol v1 are unchanged. The sibling main-server
project requires no corresponding contract or implementation change.
