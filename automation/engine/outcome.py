"""Task outcome vocabulary and the rule that protects a sent publish click."""

from enum import Enum


class PublicationStatus(str, Enum):
    RUNNING = "running"
    PUBLISHED = "published"
    COMPLETED = "completed"
    PENDING_PROFILE_VERIFICATION = "pending_profile_verification"
    UNCERTAIN = "uncertain"
    NEEDS_REVIEW = "needs_review"
    FAILED_BEFORE_PUBLISH = "failed_before_publish"
    FAILED_AFTER_PUBLISH = "failed_after_publish"
    SKIPPED_AUTH_REQUIRED = "skipped_auth_required"
    SKIPPED_SESSION_UNVERIFIED = "skipped_session_unverified"
    SKIPPED_PERSONAL_PROFILE = "skipped_personal_profile"


SUCCESS_STATUSES = frozenset({PublicationStatus.PUBLISHED, PublicationStatus.COMPLETED})


def resolve_status(status, *, publish_clicked: bool) -> PublicationStatus:
    """Validate a status and apply the post-click rule.

    Once the publish click may have been sent, a failure can no longer prove
    that nothing was published, so it becomes uncertain and is never retried
    automatically.
    """
    resolved = PublicationStatus(status)
    if publish_clicked and resolved == PublicationStatus.FAILED_BEFORE_PUBLISH:
        return PublicationStatus.UNCERTAIN
    return resolved
