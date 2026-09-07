import asyncio
import time
from collections import defaultdict

from app.domain.clock import SystemClock
from app.domain.models import DomainEvent, HttpCheckConfig, Monitor
from app.domain.state import MonitorStateMachine
from app.scheduler.loop import Scheduler


def make_monitor(monitor_id: str, interval_s: int = 1) -> Monitor:
    return Monitor(
        id=monitor_id,
        name=monitor_id,
        check_config=HttpCheckConfig(url="https://example.com"),
        interval_s=interval_s,
    )


async def test_per_monitor_cadence() -> None:
    counts: dict[str, int] = defaultdict(int)

    async def ticker(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]:
        counts[monitor.id] += 1
        return []

    fast = make_monitor("fast", interval_s=1)
    slow = make_monitor("slow", interval_s=2)
    scheduler = Scheduler(
        [fast, slow],
        ticker=ticker,
        clock=SystemClock(),
        jitter=False,
        max_concurrency=4,
    )
    await scheduler.start()
    await asyncio.sleep(2.3)
    await scheduler.stop()
    assert counts["fast"] >= 3
    assert counts["slow"] >= 2


async def test_disabled_monitor_is_skipped() -> None:
    counts: dict[str, int] = defaultdict(int)

    async def ticker(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]:
        counts[monitor.id] += 1
        return []

    disabled = make_monitor("off")
    disabled.enabled = False
    scheduler = Scheduler(
        [disabled, make_monitor("on")],
        ticker=ticker,
        clock=SystemClock(),
        jitter=False,
    )
    await scheduler.start()
    await asyncio.sleep(1.2)
    await scheduler.stop()
    assert "off" not in counts
    assert counts["on"] >= 1


async def test_isolation_when_one_monitor_throws() -> None:
    counts: dict[str, int] = defaultdict(int)

    async def ticker(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]:
        counts[monitor.id] += 1
        if monitor.id == "bad":
            raise RuntimeError("probe blew up")
        return []

    scheduler = Scheduler(
        [make_monitor("bad"), make_monitor("good")],
        ticker=ticker,
        clock=SystemClock(),
        jitter=False,
    )
    await scheduler.start()
    await asyncio.sleep(1.2)
    await scheduler.stop()
    assert counts["good"] >= 2
    assert counts["bad"] >= 1


async def test_concurrency_limit() -> None:
    active = 0
    peak = 0

    async def ticker(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return []

    monitors = [make_monitor(f"m{i}") for i in range(6)]
    scheduler = Scheduler(
        monitors,
        ticker=ticker,
        clock=SystemClock(),
        jitter=False,
        max_concurrency=2,
    )
    await scheduler.start()
    await asyncio.sleep(0.25)
    await scheduler.stop()
    assert peak == 2


async def test_graceful_stop() -> None:
    scheduler = Scheduler([make_monitor("a")], clock=SystemClock(), jitter=False)
    await scheduler.start()
    await asyncio.sleep(0.05)
    started = time.perf_counter()
    await scheduler.stop()
    assert time.perf_counter() - started < 0.5
