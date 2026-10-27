# Reel Creator

Local news-reel maker in **Studio Center → Reel Creator**. It writes, lays out and
renders on this machine. Only the AI writing step sends content to an online service (Gemini, with
your saved key). Pop Art downloads model weights once; photos, rendering and previews stay on the computer and never publish.

## Simple mode

1. **Paste** a news link or the article text. Optionally choose a look and add photos (up to eight).
2. **Create reel.** One scene is written per photo (the AI decides if there are none). The AI returns
   a hook headline plus two alternatives, and scene headlines and details. Every
   scene must be backed by a verbatim passage of the article; drafts that invent numbers or links are
   rejected and retried once. Urgent labels (Breaking, Hot) are only used when you pick that look.
3. **Edit** in place: pick **9:16** or **4:5**, swap the hook, edit scene text, reorder or delete
   scenes, change a scene's photo, set a brand name, logo and source line, choose music, and switch
   the progress line. Adding photos or **+ Scene** later writes text for the new scenes from the same
   article, continuing the story rather than repeating it. The phone preview shows the selected scene
   as it will render and updates as you type.
4. **Create video** (final 1080 wide) or **Quick preview** (540 wide). A finished video stays on
   screen until you edit the reel. **Use for Reel** sends the video to the Reel posting draft for
   both 9:16 and 4:5, carrying the caption and optional first comment.
   Choose accounts and post now or schedule from the draft.
   **Save video** exports the MP4.

The post caption is never written automatically: press **Generate caption** when the reel is ready
(or write your own), and use the refresh icon to rewrite it.

A link is read once, when the reel is created. More hooks, captions and added scenes are written
from that saved article text, so the website is not contacted again.

Every finished video also gets a cover image: the complete first frame, saved next to the
MP4 as `reel_<id>.jpg` and used as the video's poster in the studio.

New MP4 exports replace frame zero with a still image sampled at 3 seconds.
Only that single frame changes; frame one onward follows the existing timeline,
with no change to video duration or audio timing. Reels of 3 seconds or less use
their settled first card instead. The same image is saved as the JPEG cover.
Existing exports must be rendered again.

Drafts save automatically. Brand name, logo and source are remembered for new reels.

## Photo credits

A scene with a photo has a **Photo credit** field (for example "Photo: AP" or "Handout", up to 60 characters). It is
drawn small on the photo, right-aligned to the text column so it never sits under the Reels buttons: just above
the text on Breaking and Wire (it follows Text position), above the label on Pop Art, at the photo's bottom
edge on Hot, Politics and Clean, and at its top edge on Editorial. Scenes without a photo show no credit. In
advanced mode **Photo credit** can be recolored or hidden.

## House styles

**Save style** (next to the label) keeps the reel's look as a named house style: look, label, accent, ratio, photo
motion, progress line, music and advanced styles. It never keeps words, photos, per-scene settings or the brand
(brand, logo and source are remembered separately), and an uploaded music file is not part of it. Pick a house
style from the same menu to apply it to the reel, or as a chip on the first screen: the reel is then written for
that look and styled as saved. The menu shows the style in use, with a button to delete it; reels already made keep
their look. Up to 20 styles, each with its own name.

## Updating a developing story

**Update story** (top of the editor) takes the updated article or its link and rewrites the reel as a **new**
reel, so the one already made, and any video of it, stays as it is. The same number of scenes is written for the
same look; every scene's photo, credit, settings and styles stay in place, and the end card keeps its own. By
default the label becomes **UPDATE** and the date the new article's (or today's). The caption and first comment are
cleared, since they described the earlier version: check the scenes, then generate a new caption. The new
article is read once, like any other.

## Advanced mode

Switch **Simple / Advanced** at the top right once a reel exists (the choice is remembered on this computer).
Advanced mode adds a **Style** panel under the scenes. It edits the same reel: the look still lays out every
scene, keeps text inside the Reels safe area and shrinks it to fit; styles only override what the look draws.
A reel with no styles renders exactly as in simple mode, and AI rewrites change the words but keep the styles.

Advanced mode stays a news-reel tool, not a general video editor: it tunes the newsroom looks rather than
offering a free canvas, timeline, keyframes or extra layers.

Pick **All scenes** or the selected **Scene N**, then an element. You can also **click an element on the phone
preview**: that switches to advanced mode and selects it (brand, logo and ticker for all scenes, the rest for the
scene shown). The click targets come from `POST /api/reel-creator/layout?scene=N`, which returns each element's box
as fractions of the frame.

| Element | Font | Size | Color | Background | Entrance | Hide |
| --- | --- | --- | --- | --- | --- | --- |
| Headline, Detail | ✓ | 70–130 % of the look's size | ✓ | a box behind each line (Hot: the detail sticker) | ✓ | ✓ (its room goes to the photo or the other text) |
| Figure / mark (the big figure, quote mark, end-card ? or →) | | | ✓ | | ✓ | ✓ |
| Label (strap, pill, chip or kicker) | ✓ | | ✓ | fill of strap, pill or chip | ✓ | ✓ |
| Brand, Logo | Brand | | Brand | | ✓ | ✓ |
| Ticker (Breaking) | | | ✓ | bar | | ✓ |

Entrance is the animation (fade, rise, slide from left, pop, wipe or appear); its timing stays the look's. The first
scene opens fully drawn, so its entrances are not played. A scene's own setting wins over the reel's; **Use the reel style** removes it, and
a scene can show an element the reel hides. Brand, logo and ticker are shown for the whole reel and are styled
for all scenes. **Accent** (all scenes) recolors the look's accent.

Per scene: photo motion (zoom in, zoom out or still, instead of the reel's), how it **comes in** (crossfade, slide
or cut), **shade behind text** (0–200 % of the look's darkening, on Breaking, Hot, Wire and Politics), a background
color when it has no photo, and its length (auto from reading time, or 2–20 s). On Breaking and Wire, **Text
position** moves the scene's whole text block (label, strap, figure, headline and detail together) higher or lower,
between the logo row and the area Reels covers (or the ticker). Looks that size the photo to the text, and Hot and
Pop Art, keep their own placement. **Reset scene style** clears all
of these for the scene.

The document stores styles as `Reel.style` and `Scene.style` (role → overrides; unset fields are left out) and
`Scene.motion`, `transition`, `shade`, `backdrop` and `text_position` (unset is `null`).

## Goal: engage or read more

Pick the reel's goal before creating it (and change it later; nothing is rewritten until you ask).
The goal steers the hook headline, the scene text and the caption.

| Goal | Aim | Hook and scenes | Caption | First comment |
| --- | --- | --- | --- | --- |
| Engage | people share their own view | a neutral, thought-provoking question or real tension; balanced facts | ends with one genuine open question | none |
| Read more | people open the article | a curiosity gap from a real, specific fact; the explanation is left to the article | short, ends by pointing to the first comment | one line plus the article link |

Captions never contain a link (Facebook reduces reach for outbound links). For **Read more**, **Generate caption**
also writes the first comment, adding the article address when the reel came from a link; with pasted text
you paste the link into the comment box yourself. **Use for post** sends the comment to the posting draft's
first-comment field. The AI is told not to use engagement bait ("like if", "tag a friend").

## Looks

Seven looks, each its own composition, not a recolor. They borrow newsroom conventions (broadcast strap and ticker,
wire kicker and dateline, magazine page and serif deck) without copying any outlet's branding. The same layout adapts to both ratios: text
stays above the area Facebook covers on Reels (9:16) and uses the extra room on 4:5. On 9:16, text also keeps
150 px (at 1080 wide) clear of the right edge, where Reels shows its like, comment and share buttons; background
straps and photos may still run underneath.

Each look has its own type: headlines start large (about 84–140 px at 1080 wide, depending on the face) and shrink
together with the detail line until the scene fits; detail text never goes below 34 px.

| Look | For | Layout | Type |
| --- | --- | --- | --- |
| Breaking | urgent, developing news | full-bleed photo, red label bar and dark headline strap, scrolling source ticker; a LIVE badge only when **LIVE badge** is ticked, for coverage that is live right now along the bottom, top progress line | Anton headline, Barlow Condensed labels and ticker |
| Hot | trending stories | full-bleed photo darkened at the top, huge outlined headline with the label pill above and the detail on an accent sticker right under it, the photo clear below, segmented progress | Anton headline, Noto Sans Bold sticker |
| Politics | political news and analysis | photo on top fading into a solid text panel with label, headline and detail, segmented progress | Barlow Condensed headline, Noto Sans detail |
| Wire | straight factual reports | full-bleed photo, orange rule over a kicker line (label left, source and date right), plain bold headline and detail; Reuters/AP-style wire copy | Noto Sans Bold headline, Barlow Condensed kicker and credit |
| Editorial | explainers and analysis | cream page: red tab and kicker with date, headline, hairline and an italic serif deck on top; the photo fills the lower part; Economist-style magazine page | Source Serif Bold headline, Source Serif Italic deck |
| Pop Art | trending and entertainment news | full-color cutout on an accent halo with a white outline, over a dark halftone page (an uncut photo fills the top instead); centered brand pill; accent seam carrying the label (TRENDING) above a centered uppercase headline on black; no author footer | Noto Sans Bold headline |
| Clean | neutral updates | light news page: accent masthead rule under the brand, photo card, label chip with the date, dark headline and detail beneath, top progress line | Noto Sans throughout |

Pop Art removes photo backgrounds automatically using the small U²-Net model through the existing
OpenCV runtime. The first preview/export downloads about 4.4 MB of checksum-verified model weights
from the rembg GitHub release; photos are processed locally and never uploaded. Later uses work
offline. Derived PNGs are cached under the studio's `cutouts/` directory and reused by still previews,
motion previews and final exports; original assets are preserved. Existing transparent PNGs bypass
segmentation. Use a clear foreground portrait for best results; complex backgrounds and hair may
need an already-prepared transparent PNG. A failed download or inference shows an error rather than
silently keeping the background.

On Politics, Clean and Editorial a scene with no photo (and no background color) shows the page alone, its
text moved toward the middle, instead of an empty photo frame. The source line is left out wherever it would
repeat the brand name.

All looks fade scenes into each other, push or pull the photo slowly, animate the logo in with a
periodic shine. Text is English; characters the bundled font lacks are reported instead of drawn as boxes. Without a photo a scene gets a dark background
tinted with the look's accent color. Text is sized to fit its layout; if it cannot, the preview says
which scene is too long. On Politics, Clean and Editorial the photo grows when a scene's text is short and
shrinks to make room when it is long.

A photo that does not suit the frame (for example a wide photo in a full-bleed 9:16 look) loses its sides when
cropped. The frame button on a scene shows the **whole photo** instead, over a blurred, darkened copy of itself.

Photos move slowly by default: zoom in on one scene, out on the next. Choose **Zoom in**, **Zoom out** or
**Still** for the whole reel. The brand name at the top is one line across the full width; it shrinks to fit
and ends with an ellipsis only if it still cannot (it never runs under the LIVE badge).

The **Date** field (filled from the article's publish date, or today) appears in the Wire and Editorial kickers
and the Breaking ticker. Any character a display face lacks (Greek or Cyrillic, for example) makes that line fall back
to Noto Sans of the same weight.

## Scene types and highlights

Each scene has a type, chosen under its text (the AI picks one when it writes the reel):

| Type | Shows | Notes |
| --- | --- | --- |
| Story | headline and detail | the first scene is always a story, so its hook can be swapped |
| Figure | one large number above a short label (the headline) | the figure must appear in the article; the AI uses at most one |
| Quote | a large quotation mark, the quoted words, then "— speaker" | the AI only writes a quote when the words appear verbatim in the article and the speaker's name is in it; otherwise the scene becomes a story |
| End card | a large ? (a question) or → (pointing at the comment button) above the headline | written from the goal: Engage ends on the open question, Read more says the full story is in the first comment. It stays last; new scenes and photos go before it, and it gets no photo unless you add one. On Clean and Editorial a photo-less end card is the plain page |

**Highlight words**: one to three words of a headline drawn in the look's accent color (Breaking, and light pages
with a pale accent, put them on an accent block instead). The AI picks them from its own headline; you can change
them. Highlight words that are no longer in the headline are not drawn, and the field turns amber.

The figure, quote credit and end card are checked like every other line: no numbers that are not in the article,
and no links.

Scene length follows reading time (3.5–8 seconds); a reel is at most 90 seconds and eight scenes.

## Music

Auto uses the look's own track. You can choose any of the nine bundled tracks (with a play button to
hear them), turn music off, or upload your own MP3, WAV, M4A, Ogg or FLAC file (up to 25 MB and
10 minutes; a short clip loops under the reel).

## Requirements and limits

- FFmpeg/FFprobe with H.264 and AAC, Pillow with RAQM text layout, and the bundled fonts
  (Noto Sans, Anton, Barlow Condensed and Source Serif 4; see `fonts/DISPLAY-FONTS-PROVENANCE.md`).
  If any is missing the screen says so; use **Settings → Setup and repair**.
- Scene previews, quick previews and exports automatically use hardware OpenGL photo
  scaling when available (including NVIDIA). Software OpenGL, missing drivers, or GPU
  errors fall back to Pillow on the CPU. Text layout and overlays remain on the CPU.
  Quick-preview videos and final MP4s use NVIDIA H.264 NVENC after a successful test
  encode; unavailable or failed NVENC falls back to `libx264`. No GPU is required.
  GPU capabilities are checked independently of the OCR runtime. Restart the backend
  after installing drivers or restoring a failed GPU to check acceleration again.
- Images up to 25 MB and 20 megapixels (JPEG, PNG, WebP, AVIF, GIF, BMP, TIFF; still images only).
  Logos must be PNG. Output heights are made even for H.264 (4:5 previews are 540×676). Face detection (if OpenCV is installed) centers the crop on faces locally.
- Rendering is queued and shares the machine's admission control with automation. When
  automation, Live, maintenance or resource pressure needs the machine, a running render
  stops and goes back to the queue, then starts again by itself once the machine is free
  (after ten such restarts it stops and can be retried). Scene previews keep working while
  a video renders. Unfinished renders are verified or marked interrupted after a restart.
- Nine original soundtracks are bundled. See `backend/services/reel_creator/audio/README.md`.

## Architecture

- `reel.py` — the reel document (look, ratio, scenes, brand, music) and timing; the only thing stored or rendered.
- `renderer.py` — `Plan(reel, width).frame(t)`; posters and video frames use the same code.
- `service.py` — durable job queue, asset import, FFmpeg supervision, output verification.
- `writer.py` — grounded AI writing; `articles.py` — public-article extraction.
- `store.py` — SQLite journal (`reels`, `assets`, `jobs`, `settings`).

API: `/api/reel-creator/{capabilities,drafts,article,write,assets,poster,layout,brand,house-styles,jobs}`; local
and authenticated like the rest of the desktop API.

The older template-based studio, its drafts and Remotion tooling were removed; existing
drafts and render records from it are not migrated, and already exported videos stay in shared media.
