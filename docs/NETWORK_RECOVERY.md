# Local Reel timeout recovery

The backend automatically enrolls local Reel executions with these errors:
`reel_publish_timeout`, `reel_publish_loading_timeout`,
`reel_publish_button_timeout`, and `reel_composer_timeout`. Existing timeout
records less than 24 hours old are included, using their saved `ended_at`, not
the application start time. Older failures, Live jobs, and cloud-owned attempts
are excluded.

The recovery deadline is 24 hours after the original timeout, across midnight.
Checks are due at 5, 15, and 30 minutes, then 1 hour and hourly afterward until
the deadline. A passing check gets a confirmation check at least 30 seconds
later. Both upload and download must be **above 1 Mbps** (125 KB/s), and the
Facebook HTTPS connection check must pass. Rates are estimates from bounded
transfers to Cloudflare through the profile's assigned proxy; they do not prove
Facebook upload performance or publication. Recovery uses 1 MB in each direction
per sample, allowing a connection just above 1 Mbps to finish within the
20-second transfer timeout. It does not upload media or browser sessions.

Only one automatic measurement runs at a time. Tests wait while the account or
its proxy is in use by automation. Other accounts and queues continue. Results
expire after five minutes, and changing the assigned proxy invalidates them.
The second sample, next due time, measurement reservation, attempt archives,
retry count, and original deadline are persisted in `data/posting_queue.json`.
The background job runs every 15 seconds while the backend is running. A closed
desktop window is supported when using the independent worker/backend service;
a stopped backend cannot perform checks. On restart, overdue checks resume
without replaying every missed interval or resetting the deadline.

## Publication safety and queue admission

Network recovery never establishes whether a Reel was published. A post-click
timeout remains uncertain and displays **Network recovered · publication needs
review** after two passing samples. Use the existing Review action to confirm
Published or Not published. There is no reliable automatic exact-Reel identity
check in the current browser verifier; a missing first post or an old timestamp
is not evidence of nonpublication. Background recovery does not navigate,
refresh, or click inside the original browser composer.

Definite pre-publish failures, including reviewed Not published outcomes with
no remaining publication evidence, can re-enter the normal queue after passing
the network checks. They wait behind normal due work and use the same scheduler
leases, profile lock, iteration ordering, and container admission limits. The
network result and deadline are checked again when a lease is claimed. Passive
preparation from the old attempt is not reused. Later work for the affected
profile waits; unrelated profiles continue.

At most two automatic publishing retries may start. Preparation claims do not
consume a publishing retry. A retry that times out after Post needs outcome
review again. Another kind of failure stops recovery for review. Starting a
publishing attempt before the deadline allows that attempt to finish; the
deadline never interrupts an active publisher. No new attempt may start after
the deadline, even if it spent the entire window waiting for capacity.

Cancel recovery stops pending checks and queued retries while retaining uncertain
publication outcomes. It is available after an active attempt finishes.
Stop & cancel also stops recovery in desktop versions with bulk cancellation.
Manual Run Now supersedes automatic recovery for that
execution. Published, expired, cancelled, and retry-limit states do not restart
their automatic schedule. Previous attempt records, evidence paths, stage
histories, and scheduler lease identities are retained in the recovery archive.

Cloud jobs retain cloud retry authority and require a new cloud attempt. This
feature changes no worker wire protocol. The sibling `main-server` repository
was unavailable during implementation, so no cloud-side changes were made.
