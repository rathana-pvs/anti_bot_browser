from backend.services.resource_service import recommended_resource_mode


def test_24_gb_and_16_threads_recommends_medium():
    assert recommended_resource_mode(24, 16) == "medium"


def test_16_gb_remains_low_even_with_many_threads():
    assert recommended_resource_mode(16, 20) == "low"


def test_more_than_40_gb_and_16_threads_recommends_high():
    assert recommended_resource_mode(40.1, 16) == "high"


def test_40_gb_is_not_enough_for_high():
    assert recommended_resource_mode(40, 16) == "medium"


def test_more_than_40_gb_still_needs_16_threads_for_high():
    assert recommended_resource_mode(45.1, 15) == "medium"
