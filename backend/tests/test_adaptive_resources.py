"""Adaptive resource control: grows slowly when calm, shrinks fast under pressure."""
from backend.services import host_pressure
from backend.services.adaptive_resources import AdaptiveController, split_limit


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def sample(memory=40.0, cpu=20.0, psi=None, available_mb=None, total_mb=32000, containers=None, gpu_memory=None):
    return {
        "cpu_percent": cpu, "memory_percent": memory, "memory_total_mb": total_mb,
        "memory_available_mb": available_mb if available_mb is not None else total_mb * (100 - memory) / 100,
        "swap_percent": 0.0, "psi": psi, "gpu_percent": 0.0, "gpu_memory_percent": gpu_memory,
        "containers": containers or {},
    }


def test_split_matches_the_old_fixed_modes():
    assert split_limit(2) == (1, 2, 2)
    assert split_limit(4) == (2, 3, 4)
    assert split_limit(6) == (3, 5, 6)
    assert split_limit(1) == (1, 1, 1)


def test_grows_one_step_after_two_calm_minutes_while_busy():
    clock = Clock()
    controller = AdaptiveController(clock=clock)
    assert controller.decide(sample(), running_jobs=2).limit == 2
    clock.now = 119
    assert controller.decide(sample(), running_jobs=2).limit == 2
    clock.now = 121
    assert controller.decide(sample(), running_jobs=2).limit == 3
    clock.now = 200  # the calm timer restarted after growing
    assert controller.decide(sample(), running_jobs=3).limit == 3


def test_does_not_grow_when_the_slots_are_not_used():
    clock = Clock()
    controller = AdaptiveController(clock=clock)
    controller.decide(sample(), running_jobs=0)
    clock.now = 500
    assert controller.decide(sample(), running_jobs=1).limit == 2


def test_memory_pressure_shrinks_and_stops_starts():
    controller = AdaptiveController(start_limit=6)
    decision = controller.decide(sample(memory=78), running_jobs=6)
    assert decision.state == "pressure"
    assert decision.limit == 4
    assert decision.admit is False
    assert decision.hold is False


def test_severe_memory_pressure_holds_running_jobs():
    decision = AdaptiveController(start_limit=4).decide(
        sample(memory=60, psi={"memory": {"some": 25.0, "full": 1.0}}), running_jobs=4)
    assert decision.state == "pressure"
    assert decision.hold is True
    assert decision.elastic_scale == 0.5


def test_cpu_waiting_time_makes_it_busy_with_wider_spacing():
    decision = AdaptiveController().decide(sample(psi={"cpu": {"some": 15.0}}), running_jobs=1)
    assert decision.state == "busy"
    assert decision.start_spacing_ms == 20000
    assert decision.elastic_scale == 0.75


def test_reserve_lowers_the_targets():
    controller = AdaptiveController(reserve_percent=30)
    assert controller.decide(sample(memory=50), running_jobs=1).state == "busy"
    assert AdaptiveController(reserve_percent=0).decide(sample(memory=50), running_jobs=1).state == "calm"


def test_forecast_blocks_a_container_that_would_exceed_memory():
    controller = AdaptiveController()
    decision = controller.decide(sample(memory=70, total_mb=8000, available_mb=2400), running_jobs=1)
    assert decision.room_for_one is False
    assert decision.admit is False


def test_ceiling_respects_memory_and_the_user_maximum():
    controller = AdaptiveController(max_parallel=3, start_limit=8)
    assert controller.decide(sample(), running_jobs=8).limit == 3
    small = AdaptiveController(start_limit=8).decide(sample(total_mb=4000), running_jobs=8)
    assert small.ceiling == 1


def test_container_memory_is_learned_from_running_containers():
    controller = AdaptiveController()
    for _ in range(30):
        decision = controller.decide(sample(containers={"isolated_profile_001": {"memory_mb": 900.0}}), running_jobs=1)
    assert 850 <= decision.container_memory_mb <= 950


def test_spacing_blocks_until_it_has_passed():
    controller = AdaptiveController()
    assert controller.decide(sample(), running_jobs=0, since_last_start_ms=3000).admit is False
    assert controller.decide(sample(), running_jobs=0, since_last_start_ms=9000).admit is True


def test_psi_and_docker_sizes_are_parsed(tmp_path):
    (tmp_path / "memory").write_text(
        "some avg10=12.50 avg60=3.00 avg300=1.00 total=99\nfull avg10=2.25 avg60=0.10 avg300=0.00 total=9\n")
    assert host_pressure.read_psi("memory", tmp_path) == {"some": 12.5, "full": 2.25}
    assert host_pressure.read_psi("cpu", tmp_path) is None
    assert round(host_pressure.parse_size_mb("1.5GiB")) == 1536
    assert round(host_pressure.parse_size_mb("512MiB")) == 512
    usage = host_pressure.container_usage({
        "isolated_profile_001": {"cpu": "12.5%", "mem": "1.2GiB / 4GiB"},
        "postgres": {"cpu": "1%", "mem": "100MiB / 1GiB"},
    })
    assert list(usage) == ["isolated_profile_001"]
    assert usage["isolated_profile_001"]["cpu_percent"] == 12.5
    assert round(usage["isolated_profile_001"]["memory_mb"]) == 1229
