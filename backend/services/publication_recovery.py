"""Restore completed local publications using exact, durable attempt evidence."""
from datetime import datetime, timezone

from backend.services.execution_evidence import read_evidence_object, resolve_execution_evidence
from backend.services.queue_safety import apply_automation_permalink_result


def apply_published_result(execution, result, post_type, finished_at):
    """Apply the same terminal publication fields for normal and recovered results."""
    execution.update(status='published', stage='published', error=None,
                     published_at=finished_at, ended_at=finished_at)
    apply_automation_permalink_result(execution, result, post_type, recorded_at=finished_at)
    if result.get('first_comment') and not execution.get('comment_retry_status'):
        execution['first_comment_status'] = result['first_comment']
        execution['first_comment_method'] = result.get('first_comment_method')
        if result['first_comment'] == 'submitted_verified':
            execution['first_comment_verified_at'] = finished_at


def recover_completed_publication(profiles, execution, post_type):
    """Return false unless an exact local publisher attempt durably completed.

    An evidence_dir is only a location hint. Unlike screenshot display, restoring
    success requires attempt.json fencing even when that path was persisted.
    """
    if (post_type not in {'reel', 'photo', 'post'} or execution.get('cloud_attempt_id')
            or execution.get('review_status') or execution.get('cancel_requested_at')):
        return False
    history = execution.get('stage_history') or []
    if execution.get('status') in {'uncertain', 'needs_review'}:
        if (execution.get('scheduler_lease') or not history
                or history[-1].get('reason') != 'manager_restart_recovery'):
            return False
    elif execution.get('status') not in {'running', 'preparing'}:
        return False
    lease = execution.get('scheduler_lease') or execution.get('last_scheduler_lease') or {}
    if lease.get('kind') != 'publisher' or not lease.get('lease_id'):
        return False
    # A retry/preparer claim after this publisher supersedes its result.
    claims = [entry for entry in history if entry.get('reason') in {
        'publisher_scheduler_lease_claimed', 'preparer_scheduler_lease_claimed', 'manual_retry',
    }]
    if claims and (claims[-1].get('reason') != 'publisher_scheduler_lease_claimed'
                   or claims[-1].get('lease_id') != lease['lease_id']):
        return False
    expected = dict(execution_id=execution.get('execution_id'), lease_id=lease['lease_id'],
                    owner_token=execution.get('container_owner_token'))
    if not all(isinstance(value, str) and value for value in expected.values()):
        return False
    directory = resolve_execution_evidence(profiles, execution, post_type)
    task_name = 'FacebookReelTask' if post_type == 'reel' else 'FacebookPostTask'
    if directory is None or not directory.name.endswith('_' + task_name):
        return False
    attempt_file, result_file = directory / 'attempt.json', directory / 'result.json'
    if attempt_file.is_symlink() or result_file.is_symlink():
        return False
    if read_evidence_object(attempt_file) != expected:
        return False
    result = read_evidence_object(result_file)
    if (result.get('status') != 'published' or result.get('error')
            or result.get('run_id') != directory.name
            or result.get('publication_status') not in (None, 'published')):
        return False
    try:
        finished = datetime.fromisoformat(result['finished_at'].replace('Z', '+00:00'))
        if finished.tzinfo is None or finished > datetime.now(timezone.utc):
            return False
        if lease.get('claimed_at'):
            claimed = datetime.fromisoformat(lease['claimed_at'].replace('Z', '+00:00'))
            if claimed.tzinfo is None or finished < claimed:
                return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    previous_stage = execution.get('stage')
    now = datetime.now(timezone.utc).isoformat()
    apply_published_result(execution, result, post_type, result['finished_at'])
    execution.update(evidence_dir=str(directory), recovered_at=now, stage_updated_at=now)
    if isinstance(result.get('telemetry'), dict):
        execution['telemetry'] = result['telemetry']
    if result.get('facebook_identity') in ('page', 'personal'):
        execution['facebook_identity'] = result['facebook_identity']
    execution.setdefault('stage_history', []).append({
        'stage': 'published', 'timestamp': now, 'reason': 'completed_publication_recovered',
        'interrupted_stage': previous_stage, 'lease_id': lease['lease_id'], 'run_id': directory.name,
    })
    return True
