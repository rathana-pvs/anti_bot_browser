# Local Remotion news reel prototype

Silent English news template: headline, three key facts, takeaway, persistent
source/date and progress markers. The bundled 30-second story is fictional and
labeled as a demonstration. No paid API, voice provider, cloud upload or posting.

The `render` command is an independent prototype. Studio Center integration is
available separately through **Daily Brief** (see below).
The existing FFmpeg creator, immutable jobs and resource admission remain intact.
Run this CLI while browser automation/Live/other renders are idle; it does not
participate in the application's admission system. Concurrency is limited to two
browser render workers, but memory is not capped by the existing app watchdog.

## Try it

Requires Node 22+, npm, FFmpeg and FFprobe on PATH. Remotion may download a
Chrome Headless Shell on first render. Dependencies are pinned in package-lock.json.

```bash
cd tools/news-reel
npm ci
npm test
npm run typecheck
npm run render -- --preview
npm run render
```

Preview: `output/news-preview.mp4` (360×640). Final: `output/news-reel.mp4`
(1080×1920, 30 fps, silent H.264/yuv420p, fast-start MP4). Each export also writes
a PNG poster. Existing files are never overwritten; choose another output name.
The CLI verifies dimensions, frame rate, duration, codec and full FFmpeg decode
before copying out the MP4. Temporary render files are removed even on failure.

Use `REMOTION_BROWSER_EXECUTABLE=/absolute/path/to/chrome` to choose a browser
for CLI rendering. Rendering requires no account.

## Use real news

Copy `sample.json` and edit the brand, category, source, date and scene copy.
Use actual attribution and publication date; the renderer does not verify facts.
Use 3–6 scenes, 3–12 seconds each, at most 60 seconds total. Headlines are capped
at 72 characters, supporting copy at 150; allow no more than four words/second.
Review the rendered output for reading comfort and wrapping with your actual copy.

For a photo, place an authorized PNG/JPEG/WebP in `public/media/` and add
`"image": "media/photo.jpg"` to its scene. Paths are local only; absent images
use an original abstract library illustration intended for the sample story.
Replace all sample illustrations with relevant photos for a real story. Images
are cropped to fill the card; check subjects and embedded text in the export.
Footage, audio, automatic article extraction and AI selection are future work.

```bash
npm run render -- /absolute/path/to/story.json /absolute/path/to/story.mp4
```

Remotion license eligibility depends on organization size; check
https://www.remotion.dev/docs/license/pricing before production adoption.


## Studio Center integration

`npm run prepare:app` builds `src/app.tsx` and records a local Chrome Headless
Shell path. `app-render.mjs` is the backend-owned entry point. It consumes a
private prepared-layer manifest; it never imports browser profiles or calls a
voice/API provider. Studio Center's **Daily Brief** layout now runs it under the
existing backend job lifecycle, followed by FFmpeg muxing and verification.
The original `npm run render` command remains a standalone fictional demo.
Do not launch `app-render.mjs` directly for application exports.

Lens Report uses the same backend-owned renderer with a versioned template
manifest, four prepared layers and optional photo motion. Its overlap handles
render sequentially and FFmpeg joins them with bounded dissolves. Re-run
`npm run prepare:app` after updating `src/app.tsx`; the generated `app-runtime.json`
is required for Lens Report capability detection. The standalone fictional
`render` demo remains independent of the app templates.
