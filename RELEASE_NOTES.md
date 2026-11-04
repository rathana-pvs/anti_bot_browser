# Version 1.2.3

- Support both centered and left-editor image-post composer layouts, with
  caption readback and the existing publication checks.
- Improve Live preparation, automatic copying of the current broadcast key,
  startup/ending checks, and cleanup. Broadcast senders now have a hard
  120-second limit, with ending started at 100 seconds.
- When a Live comment is supplied, confirm Live, end the broadcast, then post
  and verify the comment. A recent-profile-post fallback handles ended pages
  without a comment input. Unconfirmed comments remain for review.
- Retain fuller Live screenshots and diagnostic evidence for successful
  comments and failed checks. Review screenshots before sharing: they may show
  stream credentials and account content.
- Add Auto, Extreme and Custom resource modes, including separate post and Live
  concurrency limits within a shared account limit. Existing Low/Medium/High
  settings migrate to equivalent Custom limits.
- Improve account-health warnings, OCR recovery, queue controls and handling of
  Facebook feed notices.

**Linux only.** After installing, run **Settings → Setup and repair → Install /
update runtime** when prompted. Existing profiles, sessions and settings remain
in persistent app storage.

# Version 1.2.2

- Fix proxy download/upload speed checks failing in the packaged Linux app when
  its bundled OpenSSL conflicts with the system curl libraries. Facebook
  connection and proxy location checks use the same corrected environment.
- Proxy timeouts and provider failures can still occur; this update fixes the
  library conflict that prevented the checks from running.

**Linux only.** After installing, run **Settings → Setup and repair → Install /
update runtime** when prompted. Existing profiles, sessions and settings remain
in persistent app storage.

# Version 1.2.1

- Export a complete batch support ZIP from Posting Queue → Batch → Export batch
  report ZIP. Includes all outcomes, including successful posts, errors, skipped
  items, and items needing review, with per-item logs, stage history and telemetry.
- Optionally include post content and available evidence screenshots. The report
  includes a readable summary, structured JSON and interpretation instructions
  for support. Browser profiles and session directories are excluded; diagnostic
  credentials and stream keys are redacted.
- Live setup warnings identify the account names and IDs sharing a Facebook
  stream key, making duplicate destinations easier to correct.
- Keep local Android signing credentials and device identity files out of Git.

**Linux only.** After installing, run **Settings → Setup and repair → Install /
update runtime** when prompted. Existing profiles, sessions and settings remain
in persistent app storage.

# Version 1.2.0

**Linux only.** Windows and WSL are no longer supported: there is no Windows
installer, and Windows installs will not receive this update.

Accounts and automation
- Auto warming: one switch in Settings → Resources keeps every account active
  with short read-only visits (feed, the Page, Reels, notifications). It uses
  spare capacity only, skips accounts that need attention, and always gives way
  to posts.
- Account health stages shown as icons and as locked system folders: New,
  Warming up, Ready, Resting, Needs attention and On hold. The post creator
  suggests which accounts to post with; only On hold is a hard block.
- On hold: put an account on hold before changing its proxy. Nothing automatic
  runs on it. After a proxy change the account restarts gently for 24 hours,
  and its posts wait for one warming visit from the new IP.
- Needs attention lists its evidence; each item opens the queue item and its
  screenshots. After fixing an account, Check now runs one read-only visit and
  moves it out of Needs attention when Facebook shows the Page again.
- The personal profile showing instead of the Page is detected; posting is
  skipped and the account goes to Needs attention.
- Natural session is on for every account, and the Meta account notice is
  dismissed safely.
- Reel uploads on slow proxies keep waiting while the upload is still moving
  (up to 4 minutes) instead of timing out early.
- Warm-up is a simple secondary option: what to do and how long.
- Cancelled and stopped items can be retried. A warming visit interrupted by
  an app restart is no longer reported as an unconfirmed post.

Storage
- Daily housekeeping keeps the data folder from growing: evidence is kept by
  what it proves (never for unconfirmed posts or open problems), screenshots
  are shrunk, browser caches are trimmed, old runtime backups and finished
  batches are cleaned up. Settings → Storage shows usage, progress, Preview and
  Clean now. Below 3 GB free, new queue browsers wait.

Setup
- The Linux installer stops in the first minute with a clear reason on an
  unsuitable PC (processor type, proxy tunnel device, internet, 25 GB free
  disk), accepts Ubuntu-based systems, asks for administrator approval once,
  and ends by saying whether OCR uses the GPU or the CPU.
- Setup and repair asks for confirmation before it opens.
- Saving a support ZIP now works on Linux.

After installing, run **Settings → Setup and repair → Install / update runtime**.

# Version 1.1.5

- Fix Linux setup on computers whose system Python is 3.14. The OCR runtime now
  uses the tested Python 3.12 interpreter instead of the system default.
- Reuse a compatible local Python or download a private interpreter when needed.
  Existing incompatible virtual environments are backed up; system Python,
  profiles, sessions, models, and settings are preserved.
- Show the underlying setup error alongside its exit code. Setup completion is
  checked against the current run rather than an older installation log.

After installing, run **Settings → Setup and repair → Install / update runtime**.
Preparing a private Python runtime requires internet access when Python 3.12 is
not already available. This local build does not publish an app update.

# Version 1.1.4

- Choose the primary OCR engine beside the manager's global resource controls: V5 + Tesseract (default), V6 + Tesseract (speed), or EasyOCR + Tesseract (legacy/generic).
- Save the OCR choice across launches. Restart the app/backend to apply a change; active jobs keep their startup engine.
- Use the selected engine consistently in the shared worker and local fallback, with separate model caches and engine provenance in telemetry.
- Install verified, pinned PaddleOCR models during setup and use local assets during recognition. Runtime repair is required after upgrading to install the new dependencies.
- Recognize `about a minute ago` as a one-minute timestamp, including attributed post headers. Old and conflicting timestamps remain rejected.
- Include the existing reliability improvements and signed Settings > App updates flow from the 1.1.3 source build.

Linux validation: 918 Python/evaluation tests passed (7 skipped), 68 frontend tests passed, 8 desktop tests passed, and the production frontend/desktop build succeeded. The installed Linux app passed selection persistence checks and CPU smoke checks for all three OCR engines. The timestamp suite passed in the installed runtime. These checks do not establish full publication-workflow success or manual Windows/WSL installer validation.

After installing, use Settings > Setup and repair to update the runtime when prompted. Existing profiles, media, queues, and Brain selections remain in persistent app storage.
