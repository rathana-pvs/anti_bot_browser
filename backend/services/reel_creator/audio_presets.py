from pathlib import Path

AUDIO_DIR = Path(__file__).parent / 'audio'
PRESETS = {
    'breaking': {'name': 'Breaking pulse', 'description': 'Urgent pulse and restrained news percussion.', 'duration': 20},
    'urgent_wire': {'name': 'Urgent wire', 'description': 'Fast electronic ticks and driving bass for developing stories.', 'duration': 30},
    'alert_desk': {'name': 'Alert desk', 'description': 'Sharp newsroom percussion with a confident brass-like pulse.', 'duration': 30},
    'live_update': {'name': 'Live update', 'description': 'Light, quick-moving rhythm for rolling coverage and short updates.', 'duration': 30},
    'developing_story': {'name': 'Developing story', 'description': 'Slow-building tension for unfolding events and investigations.', 'duration': 30},
    'major_headline': {'name': 'Major headline', 'description': 'Broad cinematic chords and steady drums for major announcements.', 'duration': 30},
    'washington_watch': {'name': 'Washington watch', 'description': 'Restrained piano-like notes and a low pulse for US political coverage.', 'duration': 30},
    'headlines': {'name': 'Daily headlines', 'description': 'A brighter, steady general-news bed.', 'duration': 20},
    'politics': {'name': 'Politics briefing', 'description': 'Measured low-key backing for political reporting.', 'duration': 20},
}


def preset_path(preset):
    if preset not in PRESETS:
        raise ValueError('Unknown news audio preset.')
    path = AUDIO_DIR / f'{preset}-v1.wav'
    if not path.is_file():
        raise ValueError('Bundled news audio is missing. Install the updated app or repair the runtime.')
    return path
