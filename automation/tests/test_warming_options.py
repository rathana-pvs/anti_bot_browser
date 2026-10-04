import pytest

from warming_options import normalize_warming_options


@pytest.mark.parametrize('options', [
    [], {"min_scrolls": 9, "max_scrolls": 2}, {"max_seconds": 0},
    {"max_seconds": 901}, {"min_scrolls": True}, {"max_scrolls": 1.5},
    {"random_scrolls": "false"}, {"pace": "invalid"}, {"surface": "other"},
    {"reread": None}, {"unknown": True},
])
def test_invalid_preferences_rejected(options):
    with pytest.raises(ValueError):
        normalize_warming_options(options)


def test_legacy_defaults_and_no_mutation():
    value = {"surface": "profile", "max_seconds": 60}
    options = normalize_warming_options(value)
    assert options['random_scrolls'] is False
    assert options['surface'] == 'profile'
    assert options['max_seconds'] == 60
    assert value == {"surface": "profile", "max_seconds": 60}
    options['surface'] = 'news_feed'
    assert normalize_warming_options()['surface'] == 'random'
