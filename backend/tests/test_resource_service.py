from backend.services.resource_service import recommended_resource_mode


def test_24_gb_and_16_threads_recommends_medium():
    assert recommended_resource_mode(24, 16) == "medium"


def test_16_gb_remains_low_even_with_many_threads():
    assert recommended_resource_mode(16, 20) == "low"
