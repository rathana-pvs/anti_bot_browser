# Facebook Live from an uploaded video

Implemented locally on the **Studio Center → Facebook Live** tab with its own broadcast queue. Choose a saved video or upload an
MP4, MOV, or WebM, save the Facebook server URL and key in **Edit profile**, and start a broadcast batch on **Studio Center → Facebook Live → New broadcast**.
No camera, virtual camera, or OBS installation is needed.

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

The video is sent at real-time speed as 720p H.264/AAC. Playback defaults to once
with original audio. Choose **Loop video** to repeat until a required duration
limit, or set an optional limit to shorten once playback. Choose **Muted** to use
a silent audio track. Limits are between 1 and 240 minutes in the UI; the encoder
always enforces a maximum of four hours even if the backend exits.
Silent files receive a silent audio track. The application currently caps files
at four hours. Encoding uses two CPU threads inside the account container and
therefore follows that profile's direct/proxy network route and Docker limits.

**Current limits:** The visual integration supports English desktop Live Producer
controls and requires a prepared broadcast; saving a persistent key does not create
all future broadcast metadata automatically. Incoming video begins while Facebook
previews it, so initial seconds may be consumed before public start. A waiting
slate/full-file switch is not implemented. Facebook UI changes or missing/disabled
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
Photo/Reel batches stay on Campaigns. **Playback** offers once/loop, duration limit,
and original/muted audio. Use Start now or Scheduled; advanced pacing stays collapsed.
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

Interrupted broadcasts require **Review & Resolve** in the queue, with explicit
confirmation that Facebook has ended the broadcast. An unresolved Live item cannot
be restarted through Run now. Remote/cloud batch Live is not part of protocol v1.
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
  container cleanup, and a new Live session stays blocked until the user confirms
  on Facebook that the old broadcast ended. Cleanup ownership must also be clear.
- This service can run in the independent worker/backend process. Closing the
  desktop is supported only when it is attached to that independent service;
  exiting a desktop-owned backend interrupts the stream and requires review.

## API

All endpoints are under `/api/profiles/{profile_id}/live` and inherit the desktop
session middleware. `GET /settings`, `PUT /settings`, `GET /status`,
`POST /start` (`filename`, optional `loop`, `muted`, `max_duration_seconds`), `POST /stop`, and `POST /review`
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
