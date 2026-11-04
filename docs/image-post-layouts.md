# Image-post composer layouts

Image posts recognize their editor layout separately from their publishing flow.
`automation/modules/post_composer_layouts.py` contains the ordered layout handlers;
the task uses the matching handler's caption target only after stable detection.

Supported layouts:

| Layout | Caption recognition |
| --- | --- |
| `centered_dialog` | Create post title above a single caption prompt in the central dialog |
| `left_editor_preview` | Left-side Create post title, at least two distinct toolbar controls, a single caption prompt, and Uploaded media below it |

The left editor accepts Location and Collaborator even when People is unreadable.
OCR icon prefixes are permitted on toolbar labels. The right preview is excluded
from this handler's search area. Missing structural checks or multiple eligible
caption fields cause detection to stop without entering text.

To support another layout, add a handler with its own OCR region and structural
checks to `POST_COMPOSER_LAYOUTS`. Return a `CaptionMatch` with the layout ID,
target, search-region name, and confidence. Keep publication actions out of layout
handlers: the existing P1 direct-Post and P2 Next/review templates still select
their flow from enabled action buttons after caption entry and review.

Locator telemetry records the matching layout, and the caption-entry timeline log
includes its ID and target. Caption readback, single publication, and uncertain
publication handling remain shared between layouts.

The FB29 regression case uses OCR observations from its saved failure screenshot.
It verifies acceptance of the left editor with `Peopl` and `e+ Collaborator`, and
rejection when supporting anchors are missing, the prompt is in the right preview,
or multiple editor prompts are present. Screenshot replay verifies recognition
without publishing a new post.
