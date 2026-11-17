"""When each narrated word is spoken, estimated locally: no aligner, no extra cost.

A clip's length is shared across its words by syllable count, and sentence ends are anchored to the pauses FFmpeg
finds in the audio. Captions then show two to four words at a time, the spoken word in the accent colour.
"""
import re
import subprocess

WORD = re.compile(r"\S+")
SILENCE = re.compile(r'silence_(start|end): (-?\d+(?:\.\d+)?)')


def syllables(word):
    letters = re.sub(r'[^a-z]', '', word.lower())
    if not letters:
        return 1 if any(c.isdigit() for c in word) else 0
    groups = re.findall(r'[aeiouy]+', letters)
    count = len(groups) - (1 if letters.endswith('e') and len(groups) > 1 and not letters.endswith('le') else 0)
    digits = sum(c.isdigit() for c in word)
    return max(1, count) + digits  # spoken numbers take longer


def find_pauses(path, ffmpeg='ffmpeg', min_silence=.18):
    """(start, end) of each pause in the clip, from FFmpeg's silencedetect."""
    result = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-i', str(path), '-af', f'silencedetect=noise=-35dB:d={min_silence}',
                             '-f', 'null', '-'], capture_output=True, text=True, timeout=30)
    pauses, start = [], None
    for kind, value in SILENCE.findall(result.stderr):
        if kind == 'start':
            start = max(0., float(value))
        elif start is not None:
            pauses.append((start, float(value)))
            start = None
    return pauses


def word_timings(text, seconds, pauses=()):
    """[(word, start, end)] covering the clip, in order."""
    words = WORD.findall(text)
    if not words or seconds <= 0:
        return []
    lead = pauses[0][1] if pauses and pauses[0][0] <= .05 else 0.  # silence before the first word
    tail = seconds - pauses[-1][0] if pauses and pauses[-1][1] >= seconds - .05 else 0.
    inner = [p for p in pauses if p[0] > lead + .05 and p[1] < seconds - tail - .05]
    # Sentence ends meet the inner pauses in order; words between them share that stretch by syllables.
    ends = [i for i, w in enumerate(words) if re.search(r'[.!?;:,]["”’)]?$', w)][:len(inner)]
    stretches, first, clock = [], 0, lead
    for end_index, pause in zip(ends, inner):
        stretches.append((first, end_index + 1, clock, pause[0]))
        first, clock = end_index + 1, pause[1]
    stretches.append((first, len(words), clock, seconds - tail))
    out = []
    for a, b, start, stop in stretches:
        group = words[a:b]
        if not group:
            continue
        weights = [syllables(w) or 1 for w in group]
        total, at = sum(weights), start
        for word, weight in zip(group, weights):
            span = max(0., stop - start) * weight / total
            out.append((word, round(at, 3), round(at + span, 3)))
            at += span
    return out


def caption_chunks(timings, max_words=4):
    """[(words, start, end)]: each sentence split into even groups of at most `max_words`, so no caption crosses a sentence."""
    sentences, current = [], []
    for item in timings:
        current.append(item)
        if re.search(r'[.!?;:]["”’)]?$', item[0]):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    chunks = []
    for sentence in sentences:
        count = -(-len(sentence) // max_words)  # as few groups as fit, then as even as possible
        size, extra = divmod(len(sentence), count)
        at = 0
        for k in range(count):
            take = size + (1 if k < extra else 0)
            chunks.append(sentence[at:at + take])
            at += take
    return [([w for w, _, _ in c], c[0][1], c[-1][2]) for c in chunks]
