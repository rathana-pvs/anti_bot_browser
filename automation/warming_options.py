"""Validated passive browsing preferences shared by API and runner."""

DEFAULT_WARMING_OPTIONS = {
    "random_scrolls": False,
    "min_scrolls": 2,
    "max_scrolls": 10,
    "pace": "balanced",
    "surface": "random",
    "reread": False,
    "long_breaks": False,
    "cursor_movement": True,
    "return_to_top": True,
    "max_seconds": 180,
}


def normalize_warming_options(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("warming_options must be an object")
    unknown = set(value) - set(DEFAULT_WARMING_OPTIONS)
    if unknown:
        raise ValueError(f"Unknown warming options: {', '.join(sorted(unknown))}")
    options = {**DEFAULT_WARMING_OPTIONS, **value}
    for key in ("random_scrolls", "reread", "long_breaks", "cursor_movement", "return_to_top"):
        if not isinstance(options[key], bool):
            raise ValueError(f"{key} must be a boolean")
    for key, lower, upper in (("min_scrolls", 1, 30), ("max_scrolls", 1, 30), ("max_seconds", 15, 900)):
        number = options[key]
        if isinstance(number, bool) or not isinstance(number, int) or not lower <= number <= upper:
            raise ValueError(f"{key} must be an integer between {lower} and {upper}")
    if options["min_scrolls"] > options["max_scrolls"]:
        raise ValueError("Minimum scroll count cannot exceed maximum")
    if options["pace"] not in ("quick", "balanced", "relaxed"):
        raise ValueError("Invalid warming pace")
    if options["surface"] not in ("random", "news_feed", "profile"):
        raise ValueError("Invalid warming surface")
    return options
