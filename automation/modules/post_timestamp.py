"""Bounded English timestamp parsing. Fuzzy words never change numeric ages."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


@dataclass(frozen=True)
class Timestamp:
    age_seconds: int
    unit: str
    score: float

    @property
    def recent(self):
        return self.unit in {'now', 'seconds', 'minutes'} and self.age_seconds < 600


UNITS = {
    'seconds': (1, ('s', 'sec', 'secs', 'second', 'seconds')),
    'minutes': (60, ('m', 'min', 'mins', 'minute', 'minutes')),
    'hours': (3600, ('h', 'hr', 'hrs', 'hour', 'hours')),
    'days': (86400, ('d', 'day', 'days')),
    'weeks': (604800, ('w', 'wk', 'wks', 'week', 'weeks')),
    'months': (2592000, ('mo', 'month', 'months')),
    'years': (31536000, ('y', 'yr', 'yrs', 'year', 'years')),
}
AGO = {'ago', 'ag0', 'a00', 'aoo', 'ano'}


def clean_text(text):
    text = unicodedata.normalize('NFKC', text or '').casefold()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def parse_timestamp(text):
    if re.search(r'[+-]\s*\d', text or ''):
        return None
    clean = clean_text(text)
    words = clean.split()
    if not words:
        return None
    # Known OCR substitutions are explicit. Unknown numbers/units are not guessed.
    substitutions = {'n0w': 'now', 'jusl': 'just', 'jusi': 'just', 'tew': 'few',
                     'mornent': 'moment', **{word: 'ago' for word in AGO}}
    corrected = ' '.join(substitutions.get(word, word) for word in words)
    if corrected in {'now', 'just now', 'justnow', 'moment ago', 'a moment ago', 'seconds ago',
                     'few seconds ago', 'a few seconds ago'}:
        return Timestamp(0, 'now', 1.0 if corrected == clean else .92)
    match = re.fullmatch(r'(\d{1,6})\s*([a-z]+)(?:\s+(ago|ag0|a00|aoo|ano))?', clean)
    if not match:
        return None
    number, word = int(match[1]), match[2]
    if match[3] is None:
        for suffix in sorted(AGO, key=len, reverse=True):
            if word.endswith(suffix) and len(word) > len(suffix):
                word = word[:-len(suffix)]
                break
    for unit, (factor, aliases) in UNITS.items():
        if word in aliases:
            return Timestamp(number * factor, unit, 1.0)
    if word in {'rninute', 'rninutes'}:
        return Timestamp(number * 60, 'minutes', .92)
    if len(word) < 4:
        return None
    scores = sorted(((max(SequenceMatcher(None, word, alias).ratio()
                          for alias in aliases if len(alias) >= 4), unit, factor)
                     for unit, (factor, aliases) in UNITS.items()), reverse=True)
    score, unit, factor = scores[0]
    if score < .82 or score - scores[1][0] < .08:
        return None
    return Timestamp(number * factor, unit, score)


def parse_header_timestamp(text):
    """Allow Facebook attribution before a timestamp, only on a header row."""
    if re.search(r'[+-]\s*\d', text or ''):
        return None
    if re.search(r'\d[.,]\d|\b\d+\s+\d', text or ''):
        return None
    parsed = parse_timestamp(text)
    if parsed is not None:
        return parsed
    words = clean_text(text).split()
    # Attribution can share the row with the timestamp. Only its trailing suffix
    # is eligible; date-like text elsewhere in a card cannot reach this function.
    for index, word in enumerate(words):
        if word[:1].isdigit() or word in {'now', 'just', 'jusl', 'jusi', 'n0w', 'justnow', 'a', 'few', 'moment'}:
            parsed = parse_timestamp(' '.join(words[index:]))
            if parsed is not None:
                return parsed
    return None
