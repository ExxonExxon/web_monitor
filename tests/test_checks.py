import httpx
import respx

from app.domain.models import EventType, Status
from app.domain.state import MonitorStateMachine
from app.probe.http import HTTPCheck
from app.probe.registry import get_strategy
from app.service.checks import run_check


async def test_registry_returns_http_check(fake_clock, monitor) -> None:
    strategy = get_strategy(monitor.check_type, clock=fake_clock)
    assert isinstance(strategy, HTTPCheck)


async def test_machine_exposes_injected_clock(monitor, fake_clock) -> None:
    machine = MonitorStateMachine(monitor, clock=fake_clock)
    assert machine.clock is fake_clock


async def test_single_failure_does_not_alert(monitor, fake_clock) -> None:
    machine = MonitorStateMachine(monitor, clock=fake_clock)
    with respx.mock:
        respx.get("https://example.com/").mock(return_value=httpx.Response(503))
        events = await run_check(monitor, machine)
    assert events == []
    assert machine.state.current_status != Status.DOWN


async def test_three_failures_then_recovery(monitor, fake_clock) -> None:
    machine = MonitorStateMachine(monitor, clock=fake_clock)
    state = {"code": 503}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(state["code"])

    with respx.mock:
        respx.get("https://example.com/").mock(side_effect=handler)
        down_events = []
        for _ in range(3):
            down_events += await run_check(monitor, machine)
        assert machine.state.current_status == Status.DOWN
        assert len(down_events) == 1
        assert down_events[0].type is EventType.SERVICE_DOWN
        assert machine.state.consecutive_failures == 3

        still_down = await run_check(monitor, machine)
        assert still_down == []

        state["code"] = 200
        up_events = await run_check(monitor, machine)
    assert machine.state.current_status == Status.UP
    assert machine.state.consecutive_failures == 0
    assert len(up_events) == 1
    assert up_events[0].type is EventType.SERVICE_RECOVERED
