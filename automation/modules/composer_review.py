"""Shared review budget: do not charge twice for unchanged text or media."""
from hashlib import sha256
from engine.behavior_timing import BehaviorTiming


class ComposerReview:
    def __init__(self, behavior):
        self.timing = BehaviorTiming(behavior)
        self._captions = set()
        self._media = set()

    def review(self, caption, media_revision, image_count, waiter):
        caption_revision = sha256(caption.encode()).hexdigest()
        media_revision = sha256(media_revision.encode()).hexdigest()
        new_caption = caption_revision not in self._captions
        new_media = media_revision not in self._media
        if not new_caption and not new_media:
            return 0.0
        # Reuse prior media inspection when only text changes, and vice versa.
        duration = self.timing.review(caption if new_caption else '', image_count if new_media else 0)
        waiter.pause(duration)
        self._captions.add(caption_revision)
        self._media.add(media_revision)
        return duration
