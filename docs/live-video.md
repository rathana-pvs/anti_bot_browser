# Facebook Live from an uploaded video

Implemented locally on the **Studio Center → Facebook Live** tab with its own broadcast queue. Choose a saved video or upload an
MP4, MOV, or WebM, save the Facebook server URL and key in **Edit profile**, and start a broadcast batch on **Studio Center → Facebook Live → New broadcast**.
No camera, virtual camera, or OBS installation is needed.

## Human-mode text entry

Automated Live title/caption entry shares the Photo/Reel policy: eligible captions
under 100 whitespace-separated words choose whole typing or whole paste; links,
100+ words, Unicode, and multiline text always use paste. Preset pinned comments
always use paste. The adapter uses profile pacing, checks existing field contents,
reads back entered text, and applies bounded review pauses before Save. It never
falls back to typing after a clipboard failure or clears a conflicting draft.

The existing preparation deadline still applies, with time reserved for Save and
retention checks. Insufficient entry time, unknown/changed drafts, focus loss, or
unconfirmed empty pinned-comment fields require operator review before streaming.
Only supported English field layouts are recognized. Existing saved-details
handling remains unchanged. Go live, End live, FFmpeg startup/playback, and broadcast
duration are independent of input pacing.

Live does not inherit screenshot evidence recording: screen/OCR observations are
transient, and input diagnostics contain only safe method/timing metadata. No
stream keys, captions, clipboard payloads, or screenshots are persisted by this
adapter. Offline tests validate the new input paths; a real Live input pilot has
not been performed. The profile_004 session inspected during this change reported
completed/broadcast_ended, but its stopped container and lack of screenshots did
not establish the current editor layout.

## Setup and use

1. Start the profile and sign into the intended Facebook account/Page.
2. In that profile's browser, prepare the broadcast in Facebook Live Producer.
   Choose its destination and **Streaming software**. Enable a persistent stream key if Facebook offers one.
3. In **Edit profile → Facebook Live**, copy the server
   URL and stream key. Optionally save the prepared Live Producer URL. Save settings.
4. Keep the prepared Live Producer page open. On **Studio Center → Facebook Live → New broadcast**,
   upload videos, enter a title and caption for each video, select configured profiles, choose playback and click **Start Live**. The app starts the profile if needed and checks the prepared setup.
5. Status progresses through preparing, sending video, waiting for Facebook,
   confirmed live, and ended. **Stop Live** stops the sender and attempts to end
   the same Facebook broadcast. The profile container is stopped during cleanup.

The video is sent at real-time speed as 720p H.264/AAC. **Automatic** is the default
for new batches and the direct profile Live control. FFprobe determines each source
video's length. Startup repeats the source until Facebook Live is visually confirmed;
the sender then restarts the source at the beginning and plays its full length once.
It ends the existing broadcast and verifies Facebook's ended state before completion.
The one RTMPS output connection stays open across startup and playback. Viewers may
see startup footage before the complete playback begins; exact public duration is
not guaranteed.

Choose **Manual** to reveal **Play once / Loop video** and a duration limit. Both
use the same confirmed startup. Manual once ends at the source length or shorter
limit; Manual loop repeats until its required limit. Duration and video progress
start with playback after confirmation, separately from startup. Audio can remain
original or be muted in either mode. Silent files receive a silent AAC track.
Limits are 1–240 minutes in the UI and four hours in the service. Startup has a
separate 190-second sender deadline; it never repeats indefinitely. Older saved
queue rows without a mode retain their manual loop/duration choices and gain the
same startup handling.

`automation/live_encoder.py` runs two bounded source phases into one continuous
FFmpeg relay inside the profile container. MPEG-TS discontinuities are remapped by
the relay to monotonic FLV timestamps. The service only signals playback after
confirmed Live, waits for the sender acknowledgement, and keeps startup and
playback progress distinct. Stop, sender failure or lost confirmation never creates
or retries a Facebook broadcast. Keys remain in stdin and inside the container.
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
The preset-comment helper is `automation/modules/live_pinned_comment.py`; it uses fresh
label, switch-color and enabled-control observations with transient native Copy checks.
On October 9, 2026 this helper saved a comment in an unpublished draft, reopened
the editor and verified the exact text, then restored the global preset disabled.
It never clicks Go Live. Titles allow 255 characters and captions 5,000. Older jobs without either
field continue to require manually prepared details. Live confirmation requires the dashboard, its Live notice and a
red end-broadcast control together. Ended confirmation handles a wrapped heading
plus the ended/return-home footer. Start/end OCR reads only the narrow left sidebar
and footer, keeping incoming video captions outside publication controls.

This task deliberately avoids upload tasks' full-screen evidence capture because
Live Producer displays stream credentials. Its templates never execute shell code,
receive stream keys or control FFmpeg. The streaming service retains profile locks,
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
and retries. Live captions are used verbatim without AI spinning. An optional **Pinned comment** is configured in Facebook’s preset editor before ingest,
then saved and reopened to verify the value. It accepts up to 1,000 characters and is
shared across the row’s selected profiles. Facebook retains this preset across broadcasts;
new jobs with a blank comment explicitly disable it. Legacy jobs without this field
leave the existing Facebook preset unchanged. Normal first comments are not supported for Live. Prepare each broadcast before its job runs,
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
- `data/live/<profile>/settings.json`: stream key and URLs, atomically written with
  mode 0600 in a private directory. These files are git-ignored. Keys are stored
  locally as plaintext protected by filesystem permissions; this is not OS-vault
  encryption. Settings GET returns only `key_saved`, never the key.
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
(`confirmed_ended: true`). A blank key during settings update preserves a saved
key. Profile IDs, media paths and Facebook RTMPS destinations are validated.

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
