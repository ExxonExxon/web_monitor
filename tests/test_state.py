from app.domain.models import (
    CheckResult,
    EventType,
    HttpCheckConfig,
    Monitor,
    MonitorState,
    Status,
)
from app.domain.state import MonitorStateMachine


def _ok(clock, *, latency_ms: float | None = None, status_code: int = 200) -> CheckResult:
    return CheckResult(
        ok=True, status_code=status_code, latency_ms=latency_ms, checked_at=clock.now()
    )


def _fail(clock, *, error: str = "connect", status_code: int | None = None) -> CheckResult:
    return CheckResult(ok=False, status_code=status_code, error=error, checked_at=clock.now())


def _machine(monitor: Monitor, clock, state: MonitorState | None = None) -> MonitorStateMachine:
    return MonitorStateMachine(monitor=monitor, state=state, clock=clock)


def test_first_ok_moves_unknown_to_up_no_event(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    assert machine.record(_ok(fake_clock)) == []
    assert machine.state.current_status == Status.UP
    assert machine.state.consecutive_failures == 0
    assert machine.state.last_check_at == fake_clock.now()


def test_blip_below_retries_produces_no_alert(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    assert machine.record(_fail(fake_clock)) == []
    assert machine.state.current_status == Status.UNKNOWN
    assert machine.state.consecutive_failures == 1


def test_ok_resets_partial_failure_count(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    machine.record(_fail(fake_clock))
    machine.record(_fail(fake_clock))
    assert machine.record(_ok(fake_clock)) == []
    assert machine.state.consecutive_failures == 0
    assert machine.state.current_status == Status.UP


def test_threshold_alerts_exactly_once(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    assert machine.record(_fail(fake_clock)) == []
    assert machine.record(_fail(fake_clock)) == []
    events = machine.record(_fail(fake_clock))
    assert len(events) == 1
    assert events[0].type == EventType.SERVICE_DOWN
    assert events[0].monitor_id == "blog"
    assert events[0].occurred_at == fake_clock.now()
    assert machine.state.current_status == Status.DOWN
    assert machine.state.down_since == fake_clock.now()
    assert machine.state.last_alert_at == fake_clock.now()
    assert machine.record(_fail(fake_clock)) == []
    assert machine.state.current_status == Status.DOWN


def test_down_payload_carries_context(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    machine.record(_fail(fake_clock))
    machine.record(_fail(fake_clock))
    events = machine.record(_fail(fake_clock, error="timeout", status_code=500))
    payload = events[0].payload
    assert payload["name"] == "Blog"
    assert payload["url"] == "https://example.com/"
    assert payload["error"] == "timeout"
    assert payload["status_code"] == 500


def test_recovery_emits_event_and_resets_state(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    machine.record(_fail(fake_clock))
    machine.record(_fail(fake_clock))
    machine.record(_fail(fake_clock))
    events = machine.record(_ok(fake_clock))
    assert len(events) == 1
    assert events[0].type == EventType.SERVICE_RECOVERED
    assert machine.state.current_status == Status.UP
    assert machine.state.consecutive_failures == 0
    assert machine.state.down_since is None


def test_recovery_payload_has_duration_and_resolved_at(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    machine.record(_fail(fake_clock))
    fake_clock.advance(5)
    machine.record(_fail(fake_clock))
    fake_clock.advance(5)
    machine.record(_fail(fake_clock))
    fake_clock.advance(30)
    events = machine.record(_ok(fake_clock))
    payload = events[0].payload
    assert payload["duration_down_s"] == 30.0
    assert payload["resolved_at"] == fake_clock.now().isoformat()


def test_restart_while_down_does_not_alert_again(fake_clock, monitor) -> None:
    first = _machine(monitor, fake_clock)
    for _ in range(3):
        first.record(_fail(fake_clock))
    restarted = _machine(monitor, fake_clock, state=first.state)
    fake_clock.advance(1000)
    assert restarted.record(_fail(fake_clock)) == []
    assert restarted.state.current_status == Status.DOWN
    assert restarted.state.last_alert_at is not None


def test_cooldown_suppresses_flap_down_but_not_recovery(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    for _ in range(3):
        machine.record(_fail(fake_clock))
    assert machine.state.last_alert_at == fake_clock.now()
    fake_clock.advance(10)
    assert machine.record(_ok(fake_clock))[0].type == EventType.SERVICE_RECOVERED
    fake_clock.advance(110)
    for _ in range(3):
        assert machine.record(_fail(fake_clock)) == []
    assert machine.state.current_status == Status.DOWN
    assert machine.state.last_alert_at is not None
    assert machine.record(_ok(fake_clock))[0].type == EventType.SERVICE_RECOVERED


def test_cooldown_expiry_allows_new_down_alert(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    for _ in range(3):
        machine.record(_fail(fake_clock))
    fake_clock.advance(10)
    machine.record(_ok(fake_clock))
    fake_clock.advance(400)
    events = []
    for _ in range(3):
        events.extend(machine.record(_fail(fake_clock)))
    down_events = [event for event in events if event.type == EventType.SERVICE_DOWN]
    assert len(down_events) == 1


def test_zero_cooldown_disables_suppression(fake_clock) -> None:
    monitor = Monitor(
        id="blog",
        name="Blog",
        check_config=HttpCheckConfig(url="https://example.com"),
        retries=1,
        cooldown_s=0,
    )
    machine = _machine(monitor, fake_clock)
    assert machine.record(_fail(fake_clock))[0].type == EventType.SERVICE_DOWN
    fake_clock.advance(1)
    machine.record(_ok(fake_clock))
    fake_clock.advance(1)
    assert machine.record(_fail(fake_clock))[0].type == EventType.SERVICE_DOWN


def test_paused_ignores_results(fake_clock, monitor) -> None:
    state = MonitorState(monitor_id="blog", current_status=Status.PAUSED)
    machine = _machine(monitor, fake_clock, state=state)
    assert machine.record(_fail(fake_clock)) == []
    assert machine.record(_ok(fake_clock)) == []
    assert machine.state.current_status == Status.PAUSED
    assert machine.state.consecutive_failures == 0


def test_last_result_is_recorded(fake_clock, monitor) -> None:
    machine = _machine(monitor, fake_clock)
    result = _fail(fake_clock)
    machine.record(result)
    assert machine.state.last_result is not None
    assert machine.state.last_result.ok is False
    assert machine.state.last_check_at == fake_clock.now()
