# Reel Creator

Open **Studio Center → Reel Creator**. Add one to five JPEG, PNG, WebP, AVIF, GIF, BMP or TIFF photos, pick a motion style, and
optionally add text, branding, or music. Manual editing and local rendering require no AI account. Gemini generation is optional and uses your saved API key/model in Settings. FFmpeg encodes locally; Pillow/RAQM and bundled Noto fonts draw text.

## Simple story workflow

**Add photos → choose a slideshow style → add optional text → Render video.**
New drafts use five seconds per photo, gentle zoom with crossfades, a 10-second
video, and 1080×1920 at 30 fps. Photos repeat in their selected order until the
video ends. A single photo repeats its animation every five seconds. Adding
photos extends a short video enough to show each photo at least once.

The main screen contains photos, slideshow style, the expanded AI writing assistant above the video text fields, headline and **Show subtitle**.
New drafts enable **Professional news layout**: balanced headline lines, a consistent
masthead and source treatment, and category labels with contrasting text. Under
**Headline accent**, optionally enter a short phrase from the headline to tint it
with the brand color. Saved drafts retain the classic layout until you enable it.
**News branding** contains the logo, name and accent; save the preset once to reuse
it on new reels. No source or date is invented automatically.
Choose Gentle zoom, Zoom in, Zoom out, Fade, Pan left/right, or Still. Hiding the
subtitle preserves its text and enlarges the headline by about 15% when it fits;
longer headlines still shrink to fit the available space. Text already embedded
in an image is unchanged. Music and text continue across photo cycles.

**Timing & export** is visible in the right column below the render controls and contains:

- Per-photo duration (3, 5, 7 or 10 seconds) and total video length (5–60 seconds).
  Fixed-length choices leave enough time to show every photo once; Auto timing
  computes the length from photo headlines or the prepared voice recording.
- Aspect ratio: **9:16 vertical** (default), **4:5 portrait feed**, or **1:1 square**.
  Layouts and photo framing adapt to the selected canvas without stretching the image.
  Each photo remembers its placement separately for each ratio; new ratios start with
  the whole photo fitted. Crop and movement settings stay shared across ratios.
- Export width: 1080 or 720 pixels, and 24, 30 or 60 fps. At 1080 pixels wide,
  the sizes are 1080×1920, 1080×1350 and 1080×1080; at 720 they are 720×1280,
  720×900 and 720×720. Motion previews use a 360-pixel width with the same ratio
  and selected frame rate. Export verification checks dimensions and frame rate.
  Existing drafts and completed videos without a ratio remain 9:16.
- Crossfades or simple cuts, the animation switch, and preview safe-area guides.

**Advanced** starts collapsed and contains layout/category/source and music.

## Photo headlines and Auto timing

In **Media → Photo details**, enable **Custom headline** to give a photo its own
video headline. Otherwise it uses the shared headline from Story. The post caption
stays shared. Reordering or replacing a photo preserves its headline and timing.
Layout previews show the selected photo's headline; motion previews and exports
switch headlines with the photos, including repeated cycles and crossfades.

Choose **Timing & export → Each photo → Auto · headline length** to show each photo
once. Headlines of up to 6 estimated words get 3 seconds, up to 12 get 5 seconds,
up to 20 get 7 seconds, and longer headlines get 10 seconds. Unspaced scripts use
a character-based estimate. Total video length is the sum, up to 60 seconds.
**Photo details → Photo duration** can override each estimate with 3–10 seconds.
Existing fixed and fit-video timing modes retain their previous behavior.

With prepared narration, Auto timing uses the measured recording length plus a
short finishing margin, with at least 3 seconds per photo. It distributes time
by headline reading weight, keeping manually set photo durations. This does not
infer word-level or per-photo speech cues. If all photos have manual durations
that are too short for the voice, set at least one photo back to Auto.

## English narration

**Narration → English voice** is optional and off by default. Read an article in
the AI assistant (or paste its full text), then **Generate script**. Gemini uses
only the supplied article and a word budget based on the selected video length.
Review the editable script and its source passages, then **Preview voice**.
The voice reads only this script, not the headline, post caption or evidence.
You can also write the script yourself without using Gemini.

Script generation sends article text to the Gemini model configured in Settings.
Voice preparation sends the script to Microsoft's online Edge speech service
through `edge-tts`; internet is required and no speech API key is needed. US English
male/female voices and slower/normal/faster speed are available. Voice settings
start collapsed. The provider has a bounded request timeout and failures remain
visible; nothing is silently substituted or omitted.

Prepared audio is stored locally and reused for previews, exports, retries and
app restarts. Editing the script, voice or speed requires preparing the voice
again. Measured speech must finish before the video ends; shorten the script or
increase video length when it does not fit. Rendering never calls a voice provider.
FFmpeg mixes the saved recording with the selected music and ducks music during
speech. Setting music to None or its volume to zero retains narration. Switching
narration off preserves the script and recording for later use.

New photo imports receive a local OpenCV face-framing suggestion. A fill crop is
used only when all detected faces fit above the text panel; otherwise the whole
photo is fitted. Detected-face suggestions disable photo motion so it cannot undo
the crop. **Auto frame faces** also works on existing photos. Detection can miss
faces; review the preview and adjust manually. If no face is detected, or the
detector is unavailable, the whole photo is fitted. Photos stay local.

**Adjust framing** opens a visual editor for the selected photo. Drag the photo
inside a 9:16 canvas to position it. **Crop photo** opens the original image with
eight draggable edge/corner handles and a movable selection. Arrow keys make
1% adjustments (Shift: 5%). The crop updates the layout preview live and saves
with that photo; **Done cropping** closes the controls. **Reset crop** restores
the full original. Changing a crop centers framing at 100% before Fit/Fill/Zoom
are applied. The original file is retained. Cropped-out areas are excluded from
the blurred background, motion previews and exported video. Auto face framing
analyzes the retained crop. **Apply framing to all photos** copies framing only,
preserving each photo's individual crop.

Drag the photo
inside a 9:16 canvas, or use arrow keys (Shift for larger steps), to reposition it.
The size slider and −/+ buttons cover 25–300%. **Fit whole photo** and **Fill video**
reset position/size to their preset; **Reset** centers the current mode at 100%.
Each photo keeps its own framing. **Apply framing to all photos** copies framing
without replacing their images or motion settings. The canvas marks the text area;
the main poster and encoded video use the same saved transform. Transforms render
into a bounded canvas even for extreme image aspect ratios. Old photos retain
their previous fit/fill and focal-point settings until explicitly adjusted.

**Photo details** starts collapsed and holds photo order, replacement/removal,
individual movement, the three previous motion presets, and text entrance.
Individual movement overrides the slideshow style; selecting a new slideshow
style resets those overrides. **Animate** disables photo motion, entrances and
crossfades. Subtitle visibility applies to both story and legacy layouts.

Saved drafts keep their previous evenly divided timing until a per-photo duration
is selected. Old resolution and frame-rate defaults remain 1080p/30 fps. These
controls require the updated local backend; an older backend is reported before
editing begins. Scene snapshots remain version 2.

The layout poster shows the selected photo with settled text. The real MP4
motion preview includes every photo and supports the player's pause/scrub controls.
Preview and export use the same frame-based timeline, easing, text placement,
and transition timing. Source-grounded Gemini copy and the separate post caption
continue to work with the new editor.
Headline and subtitle sizes are fitted together within the shared text panel.
AI hooks and alternative headlines are checked with their highlights using the
same layout before suggestions are returned. Copy that cannot fit at readable
minimum sizes is rejected without truncating or changing its wording.

Existing version-1 drafts and old renders keep their original behavior. Choose
**Enable story motion**, or add/replace photos, to upgrade a draft explicitly.
New scene snapshots are version 2. An older independently running backend must be
updated/restarted before the new editor can save or render them; the editor
reports that condition instead of submitting an unsupported composition.

Desktop photo imports use the same parented native picker and authenticated
streaming import as music. Cancel changes nothing. A partly successful import
keeps valid photos and reports the files that failed. Browser mode uses a regular
multiple-file picker. Thumbnails are private local cached JPEGs.

## Styles and image treatment

Inside **Text & layout**, four quick presets cover the primary workflow: **Breaking News** (red Newsroom),
**Hot News** (amber News Brief), **US Politics** (blue Editorial), and **Trump News**
(red Newsroom). They set the style, category, and accent only; your headline,
image, attribution, brand name, and other settings stay intact. These are presets
over the three templates below, not additional templates. Category labels are
operator-selected and are not inferred from an image.

- **Newsroom:** image-led layout with a readability gradient and accent.
- **Editorial:** a solid text panel; full-image treatment uses a plain background.
- **News Brief:** a gradient-backed news panel; legacy drafts use the original panel fade.

Each style supports **Keep full image**, which preserves existing artwork/text
over a background, and **Fill frame**, with horizontal/vertical focal points.
In legacy drafts, Gentle motion enlarges the background while the foreground
card stays stable. Story motion also moves the photo card within reserved margins
so the full artwork remains visible.

Headlines, supporting text, category, attribution, logo, and brand name are
optional. Khmer and English are supported by the bundled fonts. Use explicit
line breaks for Khmer without spaces; automatic dictionary-based word wrapping
is not implemented. Unsupported glyphs (including unsupported emoji) and text
overflow produce an editing message. Dates and urgency are never inferred from the image.

The safe-area guide is approximate, adjustable at the bottom, and never included
in the output. Review readability on your target platform and phone.

## Gemini news assistant

Paste a public article URL and choose **Read article**, or paste the full source
in **Full text / caption**. **Generate with Gemini** also reads the URL when needed.
The reader extracts the available article body with Trafilatura. Paywalls,
JavaScript-only pages, access blocks, and incomplete text require pasting the
source manually. Private/local addresses and redirects to them are blocked;
the reader uses no browser cookies or logged-in sessions.

Set your Google Gemini key and selected model in **Settings**. Generation sends
the source text, language, category hint, and source metadata to Google's API.
Images remain local. Keys are read at generation time and are never saved in
creator drafts or render jobs. Provider model availability and quota depend on
your Google account. See [Google's structured-output documentation](https://ai.google.dev/gemini-api/docs/structured-output).

Gemini returns a short video hook, an alternative headline, one or two highlights,
category/source metadata, style/audio suggestions, and a separate post caption
with hashtags. Highlight source passages must match the supplied text; numeric
claims absent from it are rejected. These checks are not independent fact
verification. Review the source and generated claims before applying them.
The prompt requests neutral political reporting, attribution, and preserved
uncertainty; a breaking label requires an operator-selected breaking/hot category.

**Use hook + caption** or **Use headline + caption** fills the video text and
editable post caption. Suggested style/audio applies only when selected. Source
or language changes invalidate prior suggestions. Source text, suggestions,
and reviewed captions save with the local draft. Rendering and posting do not
call Gemini again. The current caption accompanies a matching export when
handed to posting; an older export uses its saved caption snapshot.

## Included news soundtracks

Nine original synthesized beds are bundled: **Breaking pulse**, **Daily headlines**,
and **Politics briefing** (20 seconds), plus **Urgent wire**, **Alert desk**,
**Live update**, **Developing story**, **Major headline**, and **Washington watch**
(30 seconds). New drafts start with Breaking
pulse at 20% volume. Existing drafts keep their previous music or silence.
Choose a track, press **Listen**, then use the audio player to preview it. You
can adjust volume, select **No music**, or upload your own track. Custom uploads
take precedence over presets. Desktop music imports use a parented native dialog
and stream the selected file directly to the authenticated local API. Cancel
leaves the existing music, caption, and composition untouched. Browser mode
continues to use its normal file input. Built-in tracks work offline and contain no
third-party recordings or broadcaster themes; their generator and reuse terms
are in `scripts/generate_news_audio.py` and the bundled audio README.

## Preview, render, and post

The Layout tab shows a server-rendered poster using the export's layout engine.
**Preview motion** creates a 360×640 clip. **Render video** produces an H.264/yuv420p MP4 at the selected resolution and
frame rate, with fast-start metadata and optional AAC. The default is
1080×1920 at 30 fps; timing and export quality are adjustable in Advanced. Local music is volume-adjusted, padded/trimmed to
the video duration, and faded at the ends; a short track does not loop.

Drafts save automatically, with revision checks to prevent overwriting another
window's changes. New renders use immutable input/settings snapshots. Editing
during rendering does not alter that video; the editor identifies older exports.

Review the actual MP4 in the Video tab. **Export MP4** downloads it in browser
mode, or streams it directly into the native user's Downloads folder on desktop
without overwriting an existing file. The Windows command saves on the Windows
side even when the backend is in WSL. That path has unit coverage but has not
been exercised on Windows in this implementation session.

**Add to Reel post** appends a Reel row in Daily Batch Creator, preserving existing
posts, captions, profiles, and schedule. Repeated handoff of the same render does
not duplicate the row. The post caption is included on the new row. Select posting settings there; rendering never publishes.

## Jobs and resource use

One backend-owned encoder runs at a time. Exports take priority over queued
motion previews. Queued work waits for automation/Live and existing host-resource
admission checks. An active render is interrupted if automation begins or sustained
resource pressure exceeds its budget. Encoder threads and runtime are bounded;
a process-tree memory watchdog limits encoder RSS to 768 MiB. Image decoding is
bounded to 20 million pixels. This is cooperative scheduling, not a hard cgroup
memory reservation for the whole backend.

Cancellation and backend shutdown terminate the owned encoder. Startup reconciles
orphan processes using process creation time and a private attempt directory,
then marks unfinished work Interrupted. Use **Retry saved settings** to try again.
A crash after file promotion is reconciled without generating a second export.
The SQLite job journal and request IDs prevent duplicate creation after request
timeouts. The local render lifecycle does not change posting leases or recovery.

Navigating away leaves jobs running. Closing the desktop can stop its managed
backend; continuing with the window closed requires the independently managed
backend service. Completed exports remain available across backend restarts.

## Runtime and storage

Install/update the runtime through **Settings → Setup and repair**. Both Linux
and Windows/WSL installers now request FFmpeg and libraqm. Python dependencies
come from `backend/requirements.txt`. The capability banner reports missing tools,
encoders, fonts, or shaping support; use **Check again** after repair.

For a source checkout, the corresponding dependencies are:

```bash
sudo apt-get install ffmpeg libraqm0
automation/venv/bin/python -m pip install -r backend/requirements.txt
```

The renderer finds FFmpeg/FFprobe on PATH or beside the running Python executable.
Fonts and their license are bundled in `backend/services/reel_creator/fonts/`,
including in the frozen Linux backend. Runtime repair is needed on existing
installs that do not yet have host FFmpeg; Live container tools are not sufficient.

Supported photos are decoded by their content and normalized to PNG. JPEG/JFIF,
PNG, WebP, AVIF, GIF, BMP and TIFF are accepted. Animated and multi-page files
require a still frame. HEIC/HEIF and SVG are not currently supported. Image format
and pixel-limit errors are reported separately. Photos retain the 25 MiB file
and 20-million-pixel limits; logos remain PNG-only.

Inputs, drafts, journal, previews, and staging are private local files under
`DATA_DIR/reel_creator/`. That directory is ignored by Git. Only verified, completed
full-quality MP4s enter `SHARED_MEDIA_DIR`. Intermediates are removed after an
attempt; motion previews expire after seven days. Finished exports and imported
inputs are retained; there is no automatic input garbage collection or creator
delete UI in this version. Monitor disk usage for large libraries.

Images/logos are normalized PNGs with opaque asset IDs. Imports are capped at
25 MiB; audio duration is capped at ten minutes. Audio playlists and remote URLs
are unsupported. API and preview access use the existing local authentication.
The feature has no cloud job/transfer integration and never accesses browser
profile directories as media.

## Validation

Backend tests cover input validation/orientation, mixed-script layouts, revision
conflicts, concurrent request/claim/retry behavior, cancellation, PID reuse,
orphan recovery, preview retention, authentication, and incomplete-output rejection.
When host tools are available, real FFmpeg tests cover a full export with audio,
full decoding, promotion recovery, and the other templates' motion filter graphs.

```bash
automation/venv/bin/python -m pytest backend/tests/test_reel_creator.py backend/tests/test_reel_news.py backend/tests/test_reel_story.py backend/tests/test_reel_slideshow.py -q
cd manager-app
npm test
npm run build
cd src-tauri
cargo test --locked
```

The implementation session also checked the editor in an isolated local browser:
Khmer/English poster, saved drafts after reload, real 1080×1920 MP4 playback,
and repeated handoff preserving a pre-existing caption/comment without duplication.
Native export has streaming/no-overwrite/truncation unit checks. Windows/WSL,
packaged-app playback/export, and Facebook publishing acceptance remain unverified.

The desktop music-picker cancellation regression has Rust coverage for cancel
without any backend request, authenticated bounded upload, and empty-file
rejection. An isolated editor check with mocked native results confirmed cancel
preserves preset/custom music, headline and caption; import errors stay visible
inside the editor, and selecting music afterward still works. The OS dialog
itself has not been automated in this session. A reel-only error boundary also
provides recovery from render errors without removing the rest of the app.

Story validation covers frame-exact scene timing, photo order, actual text
entrances, all three motion presets, static cuts, mixed Khmer/English layers,
full decoding, and a real five-photo 1080p export with AAC and durable caption
snapshot. On this machine the five-photo preview took about 9 seconds and the
15-second full export about 47 seconds; encoder peak RSS was about 638 MiB.
These are local measurements, not guaranteed timings for other media/hardware.

## Daily Brief (Remotion)

**Text & layout → Daily Brief** selects the new vertical photo-card layout.
Add source attribution in **Source / date** and enter the actual publication date
there if desired. Dates are never invented. Your photos, crop/framing, copy,
branding, saved audio and post caption are retained when changing layouts. Photo
headlines become individual scene titles; supporting copy stays shared. Timing
uses the existing fixed/per-photo/Auto controls. The photo card has its own aspect
ratio, so review placement after changing layouts. This first version uses cuts,
text fades and a progress strip; photo-motion, transition and text-entrance
presets apply to the other three layouts. Turn Animate off for still scenes.
Daily Brief currently requires 9:16 and version-2 story motion; selecting it sets
those options. Choose another layout to export square or 4:5.

The layout poster and video use identical rasterized photo/text layers and the
bundled fonts, including the existing overflow errors. Voice remains optional;
rendering never calls a speech provider. FFmpeg combines optional prepared audio
with the Remotion video. The result goes through the same durable job journal,
one-renderer lock, admission checks, 768 MiB process-tree memory watchdog,
cancellation, full decode, atomic export and existing posting handoff. Scenes
render sequentially, with browser rendering and video encoding separated to
keep process memory bounded. No new
posting or cloud endpoint is introduced.

### Prepare the optional local runtime

From a source checkout, install Node.js 22+ and run:

```bash
cd tools/news-reel
npm ci
npm run prepare:app
```

Preparation downloads Chrome Headless Shell if needed and builds the composition
once. Actual app renders do not download dependencies or bundle source code.
Restart the source backend or refresh capabilities with **Check again**.
Daily Brief is disabled with a setup message when its runtime is unavailable;
all existing FFmpeg layouts stay available. The base Setup and repair installer
does not yet install this optional Node runtime.

For a packaged backend, prepare the runtime in a persistent external directory
and set `REEL_REMOTION_DIR` to that absolute directory before launching the backend.
It must contain the npm dependencies, `app-bundle/`, `app-render.mjs` and
`browser.json`, with the recorded browser still present. It is not embedded in
the PyInstaller binary. Packaged desktop/Windows installation has not been
verified for this optional renderer. See `tools/news-reel/README.md` for the
standalone prototype; use Studio Center for normal app rendering so its admission
and cancellation controls apply.

## Lens Report — photographic news feature

Choose **Text & layout → Lens Report**. This layout supports **9:16 and 4:5**,
with separate caption geometry for each. Photos occupy the canvas without a card
frame, with editorial Source Serif italic titles, a readability gradient, a
masthead and persistent source attribution. Add the actual source/date yourself;
no source or date is inferred. The headline font supports English/Latin copy;
unsupported glyphs produce an editing message.

In **Media**, select a photo and edit its **title** and **caption**. These default
to the shared Story headline/subtitle until overridden. **Use shared title and
subtitle** restores that inheritance. Reorder, replacement and ratio changes keep
these fields attached to their photo. Existing photo crops and independent
framing for each ratio remain available. Show subtitle controls photo captions
as well as the shared subtitle.

**Fit** preserves the entire retained image over a softened photographic
background, without motion. **Fill** can use a gentle zoom or pan. Photos with
manual zoom/offsets or Still selected remain still, preserving their placement.
**Style → Photographic motion** chooses the default movement. Photo details can
override it. With Animate enabled, headlines reveal line by line, captions follow, and photo changes dissolve
for about 0.45 seconds. Choose Cut to remove the dissolve, or disable Animate
for still scenes and cuts. Motion preview and export share these timings.

Selecting Lens Report explicitly creates a version-3 composition with layout
revision 2 and Auto timing. Revision 2 uses Globdot-inspired cream (`#F7F5EF`), ink (`#101318`), red (`#E02F2F`), gray (`#62666D`) and warm dividers (`#D6D3CB`). Auto uses both the photo title and visible caption,
with a four-second minimum estimate and ten-second maximum estimate per photo.
Overlong copy produces a reading-time message; shorten it or choose a longer
video with **Fit video** timing. Hidden captions do not count toward reading
length. Manual timing and prepared narration retain their existing controls.
Source passages/fact verification are still operator responsibilities.

Old version-1/2 drafts retain their previous rendering behavior. Switching back
to another template returns to version 2; photo captions remain saved for reuse
in Lens Report. This implementation adds the photographic template only; the
other four proposed news formats are not yet implemented.

The optional runtime now writes `app-runtime.json` with supported layout
revisions. Run `npm run prepare:app` in `tools/news-reel` after updating the source,
then restart the backend or refresh capabilities. Missing/old runtime support
leaves Lens Report disabled with a setup message. Source Serif's font and OFL
notice are bundled in `backend/services/reel_creator/fonts/`. No new paid API or
animation library is required.

Scenes and overlap handles render sequentially, with bounded FFmpeg joining,
followed by the existing audio mix/verification and durable export lifecycle.
Text-only, square and landscape Lens Report exports are not supported. Packaged
desktop/Windows optional Node runtime installation remains unverified.

Lens Report local validation (23 October 2026): seven new test cases passed
across the verification runs, plus 55 backend regression tests and 99 frontend
tests. Both ratios produced fully decoded 24 fps motion previews and five-photo
1080-wide/30 fps exports with AAC. Measured process-tree peaks were about
637 MiB for 4:5 and 669 MiB for 9:16 on the synthetic photo fixtures. The final
caption-gradient/footer adjustments were also checked with both ratio previews.
The app production build and runtime capability probe passed. These measurements
do not establish worst-case 60-second/60 fps performance or packaged-app support.


Lens Report revision 2 adds individually clipped headline and caption entrances.
**Style → Text movement** selects editorial reveal, directional slide, fade, or
None. Animate controls both text entrances and photographic motion. Existing
revision-1 drafts can explicitly select **Enable animated Lens Report**; existing
exports and queued snapshots retain their original appearance. **Layout** shows
a settled poster; select **Preview motion** and view the resulting video to see
the animation. Re-render an existing output to see the new revision.

The revision-2 palette was inspected directly from https://globdot.com/ CSS.
Only the colors inspired this layout; no news copy or branding was copied.

Revision-2 validation: all nine layout/preview tests passed, including actual
animated previews in 9:16 and 4:5 and pixel-for-pixel reconstruction of the
settled text from its animation sprites. The five motion tests, 26 backend
framing/story/timing tests and 99 frontend tests also passed. The 9:16 five-photo
1080-wide AAC export passed at approximately 657 MiB process-tree peak.
The corresponding 4:5 five-photo 1080-wide AAC export also passed, with an
approximately 624 MiB peak. Both exports passed the service's decode verification.

Lens Report revision 2 also supports **Style → Color theme → Light / Dark**.
Dark uses pure black `#000000`, cream `#F7F5EF`, muted text `#B8BBC2`, and dividers
`#34383F`, retaining the chosen accent. The theme is saved in `lens_theme` and
used by both the poster and animated render. Original revision-1 layouts retain
their original palette.

**Show playback progress line** is enabled by default for revision 2. The bottom
line fills with the accent across the whole video, including photo dissolves;
it does not restart at each photo. Its checkbox saves `show_progress`. Playback
progress remains active when text/photo Animate is off, and can be hidden
independently. Layout posters show the empty track; motion preview/export show
the fill. Re-render to update an existing output.

Dark/progress verification: seven focused tests passed, covering both ratios'
light/dark palette and complete text sprites, saved theme/progress settings,
and real dark video previews with progress sampled before, during, and after a
photo transition. The frontend production build and Remotion typecheck passed;
the running backend reports both settings in its schema and Lens Report ready.

Lens Report ratio switching preserves composition version 3 (including the
round trip between 9:16 and 4:5), saved captions, theme, and ratio-specific photo
framing. The frontend also enforces the template/version pairing on edits to
prevent the earlier validation error.

Compact 4:5 panels now fit the headline against the space left after caption
lines, avoiding a false overflow for ordinary multi-line headlines. Tests cover
the combined headline/caption budget as well as version-preserving ratio changes.

The 4:5 revision-2 header is compact: the photo starts at y=128 (canonical
1080-wide canvas), or y=96 when both brand and logo are empty. Category and
header divider move upward with it. The photo's lower edge and caption panel
stay fixed, increasing photo space. The framing editor shares these bounds;
9:16 retains its existing header spacing.

## Generate content for the selected template

In Story, provide source text or read an article URL, select the template, ratio,
caption visibility, photos, and timing, then click **Generate for this template**.
The summary shows the current settings; **Change template & settings** opens
Style. Add photos first to request one scene per photo. Before photos are added,
generation prepares one fallback scene. New template-aware generation is English
and supports Neutral, Explainer, and Concise tones.

Generation sends the article, current video text, writing tone, selected layout,
and each scene's actual timing/word/line limits to the saved Gemini model. Photos,
media IDs, profile information, and audio are excluded from the provider prompt.
The source remains untrusted data: generation preserves attribution/uncertainty,
checks source passages and unsupported numbers, and requires operator fact review.
An explicit generation action can retry once with fit feedback; provider failures
and grounding failures are reported without automatic provider retries.

Results appear under **Review suggested changes**, with current/suggested text,
source passages, and the proposed post caption. **Apply suggestions** changes the
text; **Discard** keeps the current draft. Existing manual copy and post captions
remain untouched during generation. Changes to the source, current copy, tone,
scene order/count, or content settings invalidate a pending proposal, requiring
fresh generation. Appearance/framing changes remain applicable when accepting.
Accepted scene evidence, tone, source identity, and content settings are saved in
the editorial draft. Unaccepted proposals are transient and disappear on reload.

Changing a template, ratio, timing, or caption visibility runs debounced local
layout/reading checks only. **Adapt content to new settings** explicitly requests
new wording. **Shorten this scene** changes just the selected scene and preserves
the post caption and other scenes. Lens Report has per-scene captions; other
layouts share the subtitle, so Adapt changes that shared subtitle. Theme, accent,
progress line, resolution, and photo framing changes do not request new copy.
Auto timing may change the total duration when accepted copy changes; fixed
video/photo timing is preserved. Short terminal loops and photos outside the
video duration are flagged for review.

Local endpoints `/content-fit` and `/generate-template-news` do not save drafts,
create render jobs, or publish. `/generate-news` remains available for legacy
clients. Lens Report checks and rendered layers share the same text-fitting
function. No cloud worker/protocol changes are required.

When captions are hidden, accepting generated headlines preserves existing hidden
manual captions/subtitles. They can be reviewed or adapted when made visible.

Template-aware authoring validation: the frontend suite passed 104 tests; focused
backend tests cover all five templates, both Lens Report ratios, grounded scene
output, sanitized provider context, bounded fit repair, draft metadata, endpoint
responses, and preservation of other scenes/hidden captions. The existing news
and narration regression checks passed. A real animated 4:5 preview and the app
production build passed. Provider responses were mocked; no saved Gemini key or
quota was used for tests, so live model availability/output remains unverified.

## Flexible photo scenes for Lens Report and Daily Brief

The original layouts remain available as **Style → Image layout → Original fixed
template**. New selections of Lens Report and Daily Brief use **Full frame**.
For an existing draft, choose **Enable flexible scene & motion** or change Image
layout explicitly. Existing exports and queued snapshots keep their saved layout.

- **Full frame** positions the photo across the complete canvas, with text over a
  readability gradient. Fit, Fill, crop, drag and zoom remain available in Media.
- **Adjustable photo area** uses a framed photo region. Its lower boundary moves
  with the Photo area slider; the headline and captions reflow below it.
- The **Text starts / Photo area ends** slider controls the vertical text position.
  Local fit checks flag a text area that becomes too small.
- **Motion strength** controls subtle, balanced, or larger zoom/pan travel.
  Selected motion runs from the saved photo framing, including manually dragged,
  zoomed and Fit photos. Face auto-framing's explicit Still setting stays still.
- Both templates animate title/caption lines and dissolve between scenes. Cut and
  Animate off retain their controls. Progress can be toggled independently.
- Flexible Daily Brief supports both **9:16 and 4:5**. Its original fixed card
  continues to use 9:16. Lens Report supports those two ratios in both modes.

Flexible scenes supply a source-photo plane, backdrop, independent text sprites,
and fixed branding to Remotion. Source photos are cropped once, bounded in decode
size, and positioned/animated in React at render time; their motion is not a
transform of a previously clipped photo card. The framing editor uses the same
photo bounds as the renderer. Changing the boundary also updates Gemini's content
constraints and invalidates pending text proposals when necessary.

After source changes run `npm run prepare:app` in tools/news-reel. The runtime
manifest advertises `flexible_scenes: 1`; missing support blocks flexible exports
with a preparation message while original templates remain usable.

Flexible-scene verification: geometry/manifest checks passed for both templates
and both ratios, and actual video frame comparisons demonstrated photo movement
with manual zoom and offsets, plus text entrances. The 4:5 previews were rechecked
for complete small-caption glyphs. A 1080-wide/30 fps export using 1600×2400 source
photos and AAC passed full decode verification at approximately 680 MiB process-tree
peak. Existing layout/content regression checks and the frontend suite passed;
additional framing geometry coverage and production build passed. This is a local
Linux measurement, not a worst-case guarantee for all sources or packaged Windows.

Hashtag formatting is normalized before validating a generated draft: missing
hash signs, surrounding whitespace, spaces/hyphens, trailing punctuation, and
duplicates are cleaned. Unsupported tags (including emoji/URLs) are skipped,
and at most five valid tags are retained. A cosmetic tag problem no longer
rejects the entire story or causes another paid generation attempt. Template
review shows a note if tags changed; grounding and number checks remain strict.

Flexible Daily Brief Auto timing now counts its shared subtitle as well as each
headline. Draft loading recomputes Auto timing consistently with the backend;
completed job snapshots keep their recorded duration. Reading warnings show a
**Give scenes more time** action that switches to Fit video, retains the text,
and increases the total duration if necessary (up to 60 seconds). Operators can
still choose Adapt or Shorten when they prefer to keep a short video. Duplicate
warning banners are suppressed while viewing the detailed Story checks.

Hashtag/timing fix verification: 58 focused backend tests, 107 frontend tests,
and the app production build passed. Provider responses were mocked; no Gemini
quota was spent verifying the fix.
