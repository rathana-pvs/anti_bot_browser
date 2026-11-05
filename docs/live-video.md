# Facebook Live from an uploaded video

Implemented locally on the **Studio Center → Facebook Live** tab with its own broadcast queue. Choose a saved video or upload an
MP4, MOV, or WebM, save the Facebook server and Live Producer URLs in **Edit profile**, and start a broadcast batch on **Studio Center → Facebook Live → New broadcast**.
No camera, virtual camera, or OBS installation is needed.

All broadcasts have a hard 120-second sender limit, counted from streaming
startup (not confirmation). At 100 seconds the service starts ending the existing
broadcast. An independent timer inside the container kills both encoder and
relay at 120 seconds even if the desktop is unresponsive. Source videos and old
jobs with longer duration settings are clipped to this limit. Preparation before
streaming and the comment after End are separate from the broadcast limit.
Both the usual sidebar dashboard and the full-screen dashboard's Live header
and top-right End live video control are supported. On startup errors, cleanup
ends a positively observed Live broadcast once before closing the container;
unknown Facebook outcomes remain for review.

## Human-mode text entry

Automated Live titles, captions and comments always use paste, so typing cannot use
up the 120-second preparation budget. Photo and Reel captions keep their own policy
of whole typing or whole paste.
Input checks existing drafts, reads back entered text and stops on focus
loss or clipboard failure. Conflicting title/caption drafts stop preparation.

Preparation fills only the title and caption within its 120-second budget.
When a comment is supplied, the flow is **confirm Live → End Live → post comment
→ confirm comment → stop container → success**. This also applies to older queued
jobs with the legacy `end_after_comment` flag. There is no opt-in checkbox.

After Facebook confirms Live, the app sends End, including Facebook's final End
button when a confirmation dialog appears, and stops the sender. It keeps the
browser/container open and posts the comment once on the ended video's page.
The comment has its own 75-second budget; streaming startup deadlines no longer
limit it, and neither does the two-minute broadcast limit, which is cancelled when
End begins. After Enter is sent once, the comment is confirmed when the comment box
is empty again or the exact text is visible, and no delivery notice (such as
`Posting…`, `Sending…` or `Couldn't post`) remains. The posted text is not compared
when the box is empty, because Facebook shortens links on screen. Text still in the
box or a remaining notice does not confirm success. An existing matching comment
is awaited without reposting if delivery is still pending.
If the ended Producer page has no comment input, the helper opens the profile
and uses the Reel first-post header/timestamp parser. Only the newest card with
a readable timestamp no older than three minutes is accepted; no title or
caption comparison is required. The first-post comment field is then located
with the shared Reel locator and bounded scrolling. An old/unreadable header
stops the fallback, and it never switches surfaces or submits again after a
comment submission has started. Successful evidence is the full profile frame
showing the confirmed comment. Timeline entries record the fallback and recent
post/input detection. Comment guards never read the address bar while typing.
No restored placeholder or final ended-page banner is required. Only after the
comment is confirmed and the container stops is the job marked successful.
After confirmation and evidence capture, an optional finish step randomly closes
immediately, pauses for 2–5 seconds, or clicks a recognized Facebook header logo
and performs brief warming on News Feed: pointer movement, 1–3 downward scroll
notches and a 2–4 second reading pause. Warming only starts after the browser URL
confirms News Feed opened. The timeline records feed opening and warming start
and completion. Navigation and warming together are
bounded to 12 seconds; missing controls, timeouts, cancellation and other errors
in this optional step do not change the confirmed result or cause another comment.
Container cleanup still runs afterward. This step does not post, react or edit.
The timeline records the selected action, pause duration, and completion,
skipping, timeout or failure. Optional errors use fixed diagnostic reasons and
explicitly retain Live success; failures to write these logs are also ignored.

A failed or unconfirmed comment remains for review without automatically retrying
it or starting another broadcast. Failed container cleanup also prevents success.
Facebook account checkpoints and explicit Page identity-verification dialogs
stop the attempt with `skipped_auth_required` and mark the account Needs attention.
The full restriction screenshot remains attached. A broadcast whose ending is
still unknown retains review status; a detected restriction never triggers a repost.
Blank comments keep the normal full-video playback flow. The legacy
`pinned_comment` field stores this text; comments are not pinned automatically and
Facebook's existing preset pinned-comment settings are unchanged.

On November 3, 2026 the FB05 source-code pilot verified the direct-copy key flow:
the sender used a key different from the legacy saved key, Facebook confirmed
Live, the full 60-second playback completed with encoder exit code 0, and Facebook
confirmed the broadcast ended. No copied key was saved. The comment remained
unconfirmed and was flagged for review without interrupting playback; its real
submission flow is not yet verified on Facebook. The timing fix was subsequently
verified with real Chrome, X11 paste/Enter and OCR in a network-disabled fixture,
plus a slow-observation regression using the actual entry/review controller.
No additional Facebook broadcast was started for this fix.


Failed Live browser checks retain a full browser PNG and JSON under the profile's
`automation_evidence` directory, linked from the queue item's evidence viewer.
Screenshots are saved without masking or cropping, even when no controls or
layout can be recognized. Visible stream credentials, video, titles and captions
are included as shown in the browser. Existing masked screenshots are unchanged.
JSON includes control positions/confidence and form-check counts, without raw OCR,
stream keys or clipboard payloads. The last observed frame is used so
diagnostics do not move the mouse or repeat an action. Successful observations
remain in memory during ordinary preparation/start/end actions. A confirmed
comment saves the exact full frame used to recognize the posted comment, linked
to the queue attempt before container cleanup. Evidence-write failures do not
retry the comment or change its confirmed outcome. Read-only final
inspections save a full frame even when the state is unconfirmed. After a
failed or cancelled browser action, the service attempts one bounded read-only
inspection before container cleanup if no browser evidence was returned. It never
navigates or clicks to collect that frame. Encoder failures also retain fixed,
allowlisted diagnostic codes and the exit code, without raw FFmpeg output.
When no browser observation is available (including container startup failures),
a metadata-only report records the failure stage and explicitly says that no
screenshot was available. The queue evidence viewer displays these diagnostics.
Evidence-write failures preserve the original Live error.
Offline tests validate the new input paths; a real Live input pilot has
not been performed. The profile_004 session inspected during this change reported
completed/broadcast_ended, but its stopped container and lack of screenshots did
not establish the current editor layout.

## Setup and use

1. Start the profile and sign into the intended Facebook account/Page.
2. In that profile's browser, prepare the broadcast in Facebook Live Producer.
   Choose its destination and **Streaming software**.
3. Nothing needs saving first. The app copies the current broadcast key automatically
   before sending video, and every PC account starts with the default server and Live
   Producer URLs. Change them in **Edit profile → Facebook Live** only if needed.
4. Keep the prepared Live Producer page open. On **Studio Center → Facebook Live → New broadcast**,
   upload videos, enter a title and caption for each video, select PC accounts, choose playback and click **Start Live**. The app starts the profile if needed and checks the prepared setup.
5. Status progresses through preparing, sending video, waiting for Facebook,
   confirmed live, and ended. **Stop Live** stops the sender and attempts to end
   the same Facebook broadcast. The profile container is stopped during cleanup.

The video is sent at real-time speed as 720p H.264/AAC. **Automatic** is the default
for new batches and the direct profile Live control. FFprobe determines each source
video's length. Startup repeats the source until Facebook Live is visually confirmed;
the sender then restarts the source at the beginning within the session limit.
It ends the existing broadcast and verifies Facebook's ended state before completion.
The one RTMPS output connection stays open across startup and playback. Viewers may
see startup footage before the complete playback begins; exact public duration is
not guaranteed.

Choose **Manual** to reveal **Play once / Loop video** and a duration limit. Both
use the same confirmed startup. Manual once ends at the source length or shorter
limit; Manual loop repeats until its required limit. Duration and video progress
start with playback after confirmation, separately from startup. Audio can remain
original or be muted in either mode. Silent files receive a silent AAC track.
The UI accepts 1–2 minutes. Startup and playback together have a hard 120-second
sender limit; shutdown begins at 100 seconds to leave time for Facebook's End
action. Older queue rows keep their playback choices but obey this same cap.

`automation/live_encoder.py` runs two bounded source phases into one continuous
FFmpeg relay inside the profile container. MPEG-TS discontinuities are remapped by
the relay to monotonic FLV timestamps. The service only signals playback after
confirmed Live, waits for the sender acknowledgement, and keeps startup and
playback progress distinct. Stop, sender failure or lost confirmation never creates
or retries a Facebook broadcast. The prepared key crosses only a private local
helper pipe, then goes through stdin to the container; it never enters public state.
Encoding uses two CPU threads and follows the profile's network route and limits.

**Current limits:** The visual integration supports English desktop Live Producer
controls and requires a prepared broadcast; saving a persistent key does not create
all future broadcast metadata automatically. Facebook UI changes or missing/disabled
controls require operator review. An authorized pilot on October 6, 2026 published
and ended a real broadcast: a 60-second looping stream was publicly live for 41
seconds after preview/startup. The earlier helper missed confirmation and required
manual queue resolution from Facebook's visible dashboard. The replacement
template task has regression coverage for this layout; that pilot does not prove
all accounts/layouts work. A second authorized test on October 9, 2026 published the supplied title and caption.
The old running helper missed an OCR variant of End live video; the same dashboard
was ended and the queue reconciled to Published using its verified video URL.
The updated recognition has regression coverage; this test required manual reconciliation.
No new broadcast is started merely to validate templates.

## Live task and layout templates

The browser workflow follows Reel's package and template structure:

- `automation/tasks/facebook_live.py`: the browser task with bounded prepare,
  start and end actions, fresh observations and one Go Live click per attempt.
- `automation/brains/facebook_live/bundled_default/`: validated manifest,
  executable declarative workflow, layout templates and Producer configuration.
  The backend seeds this new family into the existing Brain manager without
  replacing installed versions or its catalog.
- `automation/modules/live_producer.py`: signals observed by the host and routing
  through the shared `ComposerTemplateRegistry` and `ComposerTemplateDetector`.
  On Producer Home, preparation scrolls the main content down at most three times
  when Set up live video is below the viewport, then detects the button afresh.
  Templates cover Producer home, source selection, left/center streaming setup,
  ready to broadcast, Live dashboard and ended broadcast.
- `automation/live_browser.py`: a small CLI adapter used by the streaming service.
  It executes the selected Brain workflow and returns safe state/template metadata.

Each session pins the Brain ID, version and content digest for all three browser
actions. Changed package contents are rejected during the session. Workflow changes
apply to subsequent sessions. The durable session and queue retain Brain metadata,
selected template and verified browser state without credentials or OCR text.

Preparation fills the supplied title and caption (Facebook description) through
`automation/modules/live_post_details.py`, verifies each focused field with native
Copy, and confirms the enabled Save action and saved post details **before sending
video**. Text is passed to the helper over stdin; OCR and clipboard contents remain
transient. The opener uses the clickable “What’s your live video about?” prompt
or Edit post details, never the section heading. Post-form OCR uses its narrower
modal region. The required title label sits inside its field; the description
placeholder sits above the editable body and disappears for a saved caption.
The filled body is anchored to the freshly confirmed Create post form and title
input. Both inputs are checked before Save; the verified saved frame is reused
without a duplicate setup scan. On October 9, 2026 the complete preparation
flow was verified in the affected Facebook profile: both supplied fields matched
native Copy, Save was confirmed, and the task returned prepared without ingest
or a Go Live click.
The during-Live comment helper is `automation/modules/live_comment.py`; it verifies
an active dashboard and guarded native text input before a single submission.
The older `live_pinned_comment.py` helper remains for historical regression tests
and is no longer called during broadcast preparation.
It never clicks Go Live. Titles allow 255 characters and captions 5,000. Older jobs without either
field continue to require manually prepared details. Live confirmation requires the dashboard, its Live notice and a
red end-broadcast control together. Ended confirmation handles a wrapped heading
plus the ended/return-home footer. Start/end OCR reads only the narrow left sidebar
and footer, keeping incoming video captions outside publication controls.

Live evidence now captures the full browser screen, including any visible stream
credentials. Its templates never execute shell code,
receive stream keys or control FFmpeg. The local helper alone reads the key; it is never added to template context. The streaming service retains profile locks,
leases, playback duration, stop handling and uncertain-publication recovery.

## Batch broadcasts

Save streaming settings in **Edit profile → Facebook Live** for each target profile.
On **Studio Center → Facebook Live → New broadcast**, upload videos. All rows on this page are Live videos;
Photo/Reel batches stay on Campaigns. **Playback** defaults to Automatic. Manual reveals once/loop and a duration limit;
both modes offer original/muted audio. Use Start now or Scheduled; advanced pacing stays collapsed.
The profile selector disables profiles without valid saved Live settings. The backend
revalidates settings and media both when creating and executing the batch. Keys are
never copied into queue data or profile catalog responses.

Live jobs use the existing publisher, profile/container limits, schedules and batch
iteration order. They skip passive feed preparation. Each job applies the saved batch playback options
and retains its slot until cleanup completes. Each Live row has its own title and
caption, shared across that row’s selected profiles and preserved across scheduling
and retries. Live captions are used verbatim without AI spinning. An optional
**Comment during Live** accepts up to 1,000 characters and is shared across the
row's profiles. It is posted once after Live confirmation; pinning is manual.
Comment review is separate from the broadcast outcome. Prepare each broadcast before its job runs,
including a new prepared page for later videos on the same profile.

Interrupted broadcasts retain **Review & Resolve** in the queue for their historical outcome.
They do not block a distinct new Live job on the same profile. Before replacing
the profile status, the service archives its previous public session under
`data/live/<profile>/history/` with private permissions. Existing queue rows remain
unchanged. Profile ownership and publisher admission still exclude running work;
Facebook preflight refuses an active broadcast before ingest. Retrying the same
unresolved attempt through Run now or the scheduler still requires resolution. Remote/cloud batch Live is not part of protocol v1.
Appending Live rows to an existing batch is not supported yet; create a new batch.

## Ownership, privacy and recovery

- `backend/services/live_service.py`: local media validation, admission, encoder,
  status, stop and durable recovery. `backend/routers/live.py` exposes authenticated
  loopback-only profile endpoints. `automation/live_browser.py` invokes the Live
  task's bounded OS/OCR template checks without DOM/CDP or browser/session uploads.
- Every Live session consumes a shared publisher lease and acquires the same
  durable profile-container ownership lock as desktop and cloud automation.
  Reservations remain active during startup and cleanup. Live has a bounded
  four-hour-plus-cleanup deadline rather than the default 30-minute task deadline.
- `data/live/<profile>/settings.json`: saved server and Producer URLs, atomically
  written with mode 0600 in a private directory. Legacy saved keys are ignored for
  execution. Preparation uses the verified stream-key Copy control twice and
  requires exact matching values. Missing, changed or ambiguous reads stop before
  ingest; no cached-key fallback is permitted. The copied key stays in current-run
  memory and is not saved to disk, logs, evidence, queue or API responses.
- The actual destination is excluded with a local cross-process file lock held
  through sender/container cleanup. The filename is a digest, never a raw key.
  Duplicate-destination protection therefore uses the current copied key rather
  than old cached configuration.
- The key is sent through stdin to the container wrapper, not in host command
  arguments. It is necessarily available to FFmpeg inside its container. Raw
  encoder stderr/OCR output is not exposed in status, logs or evidence.
- `data/live/<profile>/session.json`: fsynced status without secrets. Ingest may
  trigger Facebook automatic start, so intent is durable before sending bytes.
  A crash or error after that boundary requires review and never auto-retries.
- “Live” requires visual confirmation of Facebook's dashboard, Live notice and end-broadcast control;
  encoder progress alone is labeled sending/waiting. Ended requires Facebook's
  ended confirmation. Failed end confirmation leaves review even after sender and
  container cleanup, and that attempt remains unresolved. A distinct new job may proceed through
  normal admission and Facebook preflight; actual active broadcasts still block ingest.
  Cleanup ownership must also be clear.
- This service can run in the independent worker/backend process. Closing the
  desktop is supported only when it is attached to that independent service;
  exiting a desktop-owned backend interrupts the stream and requires review.

## API

All endpoints are under `/api/profiles/{profile_id}/live` and inherit the desktop
session middleware. `GET /settings`, `PUT /settings`, `GET /status`,
`POST /start` (`filename`, optional `playback_mode` defaulting to `auto`, `loop`, `muted`, `max_duration_seconds`), `POST /stop`, and `POST /review`
(`confirmed_ended: true`). Legacy key fields remain accepted for compatibility,
but execution always uses the freshly copied key. Profile IDs, media paths and
Facebook RTMPS destinations are validated.

## Cloud impact

No worker wire schema or authoritative cloud job state is changed. The v1 worker
still accepts only photo/reel jobs; cloud scheduling and remote Live controls are
not part of this local version. Adding cloud Live requires coordinated contract,
capability, lease-duration, cancellation, progress/result and dashboard changes in
`../main-server`, with simulator/recovery validation before real execution. Stream
keys must remain local and must not be added to profile-catalog heartbeats.

## Validation

```sh
automation/venv/bin/python -m pytest backend/tests/test_live_service.py backend/tests/test_live_browser.py backend/tests/test_automation_launch.py backend/tests/test_publisher_concurrency.py -q
cd manager-app
npm run build
```

Tests cover secret isolation, trusted destinations and media containment, shared
admission/profile exclusion, crash holds/review, start/end failure boundaries,
no repeated Go Live clicks, disabled/stale/ambiguous controls and stop ownership.
The container's installed FFmpeg 4.4.2 was also exercised offline with generated
one-second videos, with and without audio, using the actual encoder arguments.
Both produced 1280×720 H.264 with AAC. Browser checks used simulated responses for
settings, video selection, Live progress and Stop; they sent no Facebook stream.

### Early stream completion diagnostics

Startup now saves timestamped preparation, start, Go Live request/click and
verification events in the session and queue logs. If the sender finishes before
confirmation, the click-capable helper is cancelled and reaped first. A bounded,
read-only final check records Live, ended or unconfirmed before container cleanup;
it never clicks Go Live. A confirmed active broadcast is ended through the normal
end workflow. Unconfirmed publication or failed end verification retains review.
A normal sender exit reports that the video finished before confirmation, distinct
from an encoder failure. No screenshots, OCR text or stream keys enter these logs.
This does not add a waiting slate or defer playback until public Live.

On October 16, 2026 an authorized diagnostic used a two-minute loop. Facebook's
persistent-key conflict warning blocked preparation until the operator confirmed
no other sender used the key and explicitly reassigned it. The broadcast then
reached the Live dashboard. Broad sidebar OCR missed the grey Live notice despite
recognizing the dashboard and red End button; a focused sidebar OCR read recovered
it. The broadcast was manually ended, ended state was visually verified by the
service, and its queue result was reconciled to Published. The task now retries
that notice in a narrow sidebar region and reports a key-conflict dialog without
automatically reassigning keys. No second broadcast was used to validate the fix.

### Automatic playback validation (1.0.13)

The new sender was verified offline in disposable network-disabled browser-image
containers, without profiles or Facebook credentials. Real FFmpeg tests cover a
1.2-second source, once playback, repeated playback, original/muted audio, a shortened limit, stopping during
startup and a startup deadline. The checks decode the final red/green/blue source
frames to verify complete playback from frame zero, inspect H.264/AAC output, and
verify monotonic audio/video packet timestamps across the single-output handoff.
Service tests cover automatic defaults, ignored hidden manual controls, manual
limits, legacy rows, confirmation before switching and ended confirmation. No real
broadcast was started to validate this new two-phase sender.

```sh
RUN_LIVE_ENCODER_OFFLINE=1 automation/venv/bin/python -m pytest backend/tests/test_live_encoder_offline.py -q
```

### Automatic end confirmation (1.0.15)

Natural completion waits for the sender to flush the full playback before requesting
End live video. The browser then confirms the colored End action and waits for
Facebook's ended state. The confirmation heading may itself read End live video;
it is excluded from button candidates by requiring enabled blue/red button color
on both the initial and fresh screen. An already-open confirmation is supported.
End requests, clicks and confirmation clicks are recorded without OCR or images.
Clicking End alone never marks a job completed; ended verification is required.
