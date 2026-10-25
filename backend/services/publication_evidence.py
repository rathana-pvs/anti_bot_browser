"""Shared publication boundary check, including explicit operator resolutions."""
FINAL_CLICK_STAGES = {"publish_clicked", "verifying"}


def has_publication_evidence(execution: dict) -> bool:
    history = [item for item in execution.get('stage_history', []) if isinstance(item, dict)]
    # Old clicks remain archived, but an explicit review permits a new attempt.
    resolved = [index for index, item in enumerate(history)
                if item.get('reason') == 'operator_resolved_not_published']
    stages = {execution.get('stage')}
    if resolved:
        history = history[resolved[-1] + 1:]
    else:
        stages.add(execution.get('last_active_stage'))
    stages.update(item.get('stage') for item in history)
    return bool(execution.get('published_at') or execution.get('publish_attempted') or
                stages & (FINAL_CLICK_STAGES | {'publish_authorized', 'pending_profile_verification', 'published', 'commenting'}))
