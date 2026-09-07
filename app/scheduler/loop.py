import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta

from app.domain.clock import Clock, SystemClock
from app.domain.models import DomainEvent, Monitor
from app.domain.state import MonitorStateMachine
from app.service.checks import run_check

logger = logging.getLogger(__name__)

CheckTicker = Callable[[Monitor, MonitorStateMachine], Awaitable[list[DomainEvent]]]


async def _default_ticker(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]:
    return await run_check(monitor, machine)


class Scheduler:
    """Async scheduler: one task per enabled monitor, fixed-phase intervals."""

    def __init__(
        self,
        monitors: Sequence[Monitor],
        *,
        ticker: CheckTicker | None = None,
        clock: Clock | None = None,
        max_concurrency: int = 5,
        jitter: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        self._ticker = ticker or _default_ticker
        self._clock = clock or SystemClock()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._rng = rng or random.Random()
        self._jitter = jitter
        self._enabled = [monitor for monitor in monitors if monitor.enabled]
        self._machines = {
            monitor.id: MonitorStateMachine(monitor, clock=self._clock) for monitor in self._enabled
        }
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._tasks:
            raise RuntimeError("scheduler already started")
        self._tasks = [asyncio.create_task(self._tick_loop(monitor)) for monitor in self._enabled]

    async def stop(self) -> None:
        tasks = self._tasks
        self._tasks = []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _tick_loop(self, monitor: Monitor) -> None:
        interval = timedelta(seconds=monitor.interval_s)
        stagger = 0.0
        if self._jitter:
            stagger = self._rng.random() * monitor.interval_s
        due = self._clock.now() + timedelta(seconds=stagger)
        machine = self._machines[monitor.id]
        while True:
            delay = (due - self._clock.now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            events: list[DomainEvent] = []
            try:
                async with self._semaphore:
                    events = await self._ticker(monitor, machine)
            except Exception:
                logger.exception("check failed for monitor %s", monitor.id)
            for event in events:
                logger.info("monitor %s event: %s", monitor.id, event.type.value)
            due += interval
