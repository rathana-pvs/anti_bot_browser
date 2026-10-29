"""Resolve screenshots to an exact local queue attempt, including interrupted runs."""
import json
from datetime import datetime
from pathlib import Path


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def profile_evidence_root(profiles: Path, profile_id: str) -> Path | None:
    if not profile_id or profile_id in {'.', '..'} or Path(profile_id).name != profile_id:
        return None
    root = (profiles / profile_id / 'automation_evidence').resolve()
    return root if root.is_relative_to(profiles.resolve()) else None


def _current_history(execution: dict) -> list[dict]:
    history = execution.get('stage_history') or []
    # Retries retain older history; only the latest claimed publisher may match.
    start = 0
    for index, entry in enumerate(history):
        if entry.get('reason') in {'publisher_scheduler_lease_claimed', 'manual_retry'}:
            start = index + 1
    return history[start:]


def _same_stage(left: dict, right: dict) -> bool:
    if left.get('stage') != right.get('stage'):
        return False
    try:
        # Both values originate on the same local runner. Do not guess the
        # timezone of historical naive timestamps. Logging follows stage writes
        # by a few microseconds, so permit only a narrow 10ms difference.
        a = datetime.fromisoformat(left['timestamp'])
        b = datetime.fromisoformat(right['timestamp'])
        return abs((a - b).total_seconds()) <= 0.01
    except (KeyError, TypeError, ValueError):
        return False


def resolve_execution_evidence(profiles: Path, execution: dict, post_type: str) -> Path | None:
    root = profile_evidence_root(profiles, str(execution.get('profile_id') or ''))
    if root is None:
        return None
    raw = execution.get('evidence_dir')
    if raw:
        directory = Path(raw).resolve()
        return directory if directory != root and directory.is_relative_to(root) and directory.is_dir() else None
    if not root.is_dir():
        return None
    lease = execution.get('scheduler_lease') or execution.get('last_scheduler_lease') or {}
    task_name = {'reel': 'FacebookReelTask', 'photo': 'FacebookPostTask',
                 'post': 'FacebookPostTask', 'warming': 'FacebookWarmingTask'}.get(post_type)
    if not task_name:
        return None
    history = _current_history(execution)
    matches = []
    for directory in root.glob(f'*_{task_name}'):
        if directory.is_symlink() or not directory.is_dir():
            continue
        context_file = directory / 'attempt.json'
        if context_file.exists():
            context = _read_object(context_file)
            matched = bool(lease.get('lease_id') and execution.get('container_owner_token')
                           and context.get('execution_id') == execution.get('execution_id')
                           and context.get('lease_id') == lease.get('lease_id')
                           and context.get('owner_token') == execution.get('container_owner_token'))
        else:
            stages = _read_object(directory / 'stage.json').get('history') or []
            matched_stages = {entry.get('stage') for entry in history
                              if entry.get('stage') in {'preparing', 'composing', 'ready_to_publish',
                                  'publish_authorized', 'publish_clicked', 'verifying',
                                  'pending_profile_verification', 'commenting'}
                              and any(_same_stage(entry, saved) for saved in stages)}
            matched = len(matched_stages) >= 2
        if matched:
            matches.append(directory)
    # Never select the newest/nearest run when the association is ambiguous.
    return matches[0] if len(matches) == 1 else None
