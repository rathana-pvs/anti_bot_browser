"""Pure, versioned whole-caption entry policy. No input or provider side effects."""
from dataclasses import asdict, dataclass
import re

POLICY_VERSION = 1
# A conservative URL detector: false positives select paste, never change content.
LINK = re.compile(r"(?:https?://|www\.)\S+|(?<![\w@])(?:[^\W_](?:[\w-]*[^\W_])?\.)+(?:[^\W\d_]{2,63}|xn--[\w-]+)(?::\d+)?(?:[/#?]\S*)?", re.I)


def contains_link(text: str) -> bool:
    return bool(LINK.search(text))


def can_type(text: str) -> bool:
    # Multiline entry stays paste-only until individual field line-break semantics
    # are validated. Unicode is supported through the clipboard, not xdotool type.
    return all(' ' <= char <= '~' for char in text)


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
    words, link = len(text.split()), contains_link(text)
    if not text:
        method, reason = 'skip', 'empty'
    elif field_kind in {'link', 'comment'} or link:
        method, reason = 'paste', 'link_or_comment'
    elif words >= 100:
        method, reason = 'paste', 'long_caption'
    elif not can_type(text):
        method, reason = 'paste', 'typing_unsupported'
    else:
        paste_share = behavior.persona.paste_preference if getattr(behavior, 'natural', False) else 0.5
        method = 'type' if behavior.stream('entry_strategy').random() < 1 - paste_share else 'paste'
        reason = 'short_caption_choice'
    return EntryPlan(method, reason, words, link, wpm=behavior.typing_speed() if method == 'type' else None)
