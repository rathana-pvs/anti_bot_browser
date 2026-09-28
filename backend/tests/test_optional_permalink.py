from backend.services.queue_safety import apply_automation_permalink_result


def test_not_requested_permalink_is_not_reported_missing():
    execution = {"status": "published"}

    apply_automation_permalink_result(
        execution,
        {
            "post_url": None,
            "permalink_status": "not_requested",
            "permalink_missing": False,
            "permalink_recovery_attempted": False,
        },
    )

    assert execution["permalink_status"] == "not_requested"
    assert execution["permalink_missing"] is False
    assert execution["permalink_source"] is None
    assert execution["permalink_note"] is None
