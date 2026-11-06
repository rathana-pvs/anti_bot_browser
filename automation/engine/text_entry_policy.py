"""Pure, versioned whole-caption entry policy. No input or provider side effects."""
from dataclasses import asdict, dataclass
import re

POLICY_VERSION = 2
# A conservative URL detector: false positives select paste, never change content.
LINK = re.compile(r"(?:https?://|www\.)\S+|(?<![\w@])(?:[^\W_](?:[\w-]*[^\W_])?\.)+(?:[^\W\d_]{2,63}|xn--[\w-]+)(?::\d+)?(?:[/#?]\S*)?", re.I)


def contains_link(text: str) -> bool:
    return bool(LINK.search(text))


@dataclass(frozen=True)
class EntryPlan:
    method: str
    reason: str
    word_count: int
    contains_link: bool
    version: int = POLICY_VERSION
    wpm: int | None = None

    def summary(self):
        return asdict(self)


def choose_entry_plan(text: str, field_kind: str, behavior) -> EntryPlan:
    # Every field pastes: typing a caption ran into the composer check once the
    # placeholder was gone, which stopped reels before they were published.
    words, link = len(text.split()), contains_link(text)
    if not text:
        method, reason = 'skip', 'empty'
    elif field_kind in {'live_title', 'live_caption'}:
        method, reason = 'paste', 'live_details'
    elif field_kind in {'link', 'comment'} or link:
        method, reason = 'paste', 'link_or_comment'
    else:
        method, reason = 'paste', 'always_paste'
    return EntryPlan(method, reason, words, link)
