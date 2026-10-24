"""Deterministic reading-time estimates shared by drafts, previews and exports."""
import math
import re


def reading_seconds(headline):
    # Unspaced scripts (including Khmer) need a character-based fallback.
    headline = headline.strip()
    words = len(re.findall(r'[A-Za-z0-9]+', headline))
    words += math.ceil(sum(ord(char) > 127 for char in headline) / 5)
    return 3 if words <= 6 else 5 if words <= 12 else 7 if words <= 20 else 10


def photo_seconds(spec, photos=None):
    photos = spec.photos if photos is None else photos
    def estimate(photo):
        title = photo.headline if photo.headline is not None else spec.headline
        flexible_daily=spec.template=='daily_brief' and spec.photo_layout!='template'
        if spec.template != 'lens_report' and not flexible_daily:
            return reading_seconds(title)
        body = (photo.caption if photo.caption is not None else spec.supporting) if spec.template=='lens_report' else spec.supporting
        body = body if spec.show_subtitle else ''
        entrance=.6 if (spec.layout_revision==2 or flexible_daily) and spec.motion and spec.text_animation!='none' else 0
        return max(4, min(10, math.ceil(len((title+' '+body).split())/3 + entrance)))
    seconds = [photo.duration_seconds or estimate(photo) for photo in photos]
    if not seconds or not (spec.narration_enabled and spec.narration_id and spec.narration_duration):
        return seconds
    # A single recording has no per-photo speech cues. Distribute its measured
    # length by reading weight, preserving explicit photo durations where possible.
    minimum = [photo.duration_seconds or 3 for photo in photos]
    total = max(sum(minimum), math.ceil(spec.narration_duration + .25))
    if total > 60:
        raise ValueError('Narration is too long. Keep the video within 60 seconds.')
    flexible = [i for i, photo in enumerate(photos) if photo.duration_seconds is None]
    if not flexible:
        if sum(minimum) < total:
            raise ValueError('Photo durations are shorter than narration. Set a photo duration to Auto.')
        return minimum
    remaining = total - sum(minimum)
    weight = sum(seconds[i] for i in flexible)
    shares = {i: remaining * seconds[i] / weight for i in flexible}
    result = minimum[:]
    for i in flexible:
        result[i] += math.floor(shares[i])
    for i in sorted(flexible, key=lambda i: -(shares[i] % 1))[:total - sum(result)]:
        result[i] += 1
    return result
