"""Timestamp age is numeric and bounded; fuzzy words cannot override an old age."""
import pytest
from modules.post_timestamp import parse_timestamp, parse_header_timestamp


@pytest.mark.parametrize('text,seconds,recent', [
    ('Justnow', 0, True), ('Just now', 0, True), ('now', 0, True), ('Jusl n0w', 0, True),
    ('seconds ago', 0, True), ('a tew seconds a00', 0, True), ('a moment ago', 0, True),
    ('30 seconds ago', 30, True), ('59s', 59, True), ('600s', 600, False),
    ('1m', 60, True), ('9m', 540, True), ('9 minutes ago', 540, True),
    ('10m', 600, False), ('10 minutes ago', 600, False), ('30m', 1800, False),
    ('30 minutes ago', 1800, False), ('7Minutes Ano', 420, True),
    ('5 rninutes ago', 300, True), ('1 hour ago', 3600, False), ('2h', 7200, False),
    ('2daysago', 172800, False), ('2days ago', 172800, False), ('2 days ago', 172800, False), ('1w', 604800, False),
])
def test_timestamp_age_and_unit(text, seconds, recent):
    result = parse_timestamp(text)
    assert result is not None
    assert result.age_seconds == seconds
    assert result.recent is recent
    assert result.score >= .82


@pytest.mark.parametrize('text', ['', 'published last year', 'I minutes ago', '1O minutes ago',
    '5 blorps ago', 'news now today', 'the video says just now', '9.5 minutes ago', '-1m',
    '100 days ago just now'])
def test_unreadable_or_unrelated_words_do_not_become_new(text):
    assert parse_timestamp(text) is None


def test_header_attribution_parses_only_trailing_time():
    assert parse_header_timestamp('Published by Anna · 9m').recent
    assert not parse_header_timestamp('Published by Anna · 1 hour ago').recent
    assert parse_header_timestamp('9m and a new video') is None


@pytest.mark.parametrize('text', ['9.5 minutes ago', '9 5 minutes ago', 'Published by Editor · 9.5m'])
def test_header_does_not_guess_an_ambiguous_number(text):
    assert parse_header_timestamp(text) is None


def test_joined_timestamp_with_arbitrary_header_attribution():
    assert parse_header_timestamp('Posted by Editor · Justnow').recent
