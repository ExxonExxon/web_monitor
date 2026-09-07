# Phase C — Probing + Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship HTTP probing and the custom async scheduler, wired to the Phase B state machine, exposed to the operator via `wm check <id>`, with a green gate and mocked-network tests.

**Architecture:** A `CheckStrategy` protocol + `HTTPCheck` (httpx) implement probing. A registry maps `CheckType` → strategy factory, so nothing else in the app ever branches on check type. A shared service function `run_check(monitor, machine)` (probe → state-machine `record`) is the one code path used by both the scheduler loop and the CLI (D8). The scheduler runs one asyncio task per **enabled** monitor with a fixed-phase interval computed from the injected clock, optional initial jitter (stagger), a global concurrency semaphore, per-monitor exception isolation, and a graceful cancel-and-drain `stop()`.

**Tech Stack:** Python 3.12, `httpx` (new **runtime** dep), `respx` (new **dev** dep), asyncio, Typer, pytest-asyncio.

**Spec:** `PLAN.md` §3.1 (Scheduler/Check Strategy responsibilities), §7 (Check Strategies), §8 (reliability rules — timeouts count as failures, isolation, single clock), §11 (CLI `wm check <id>`), §13 (httpx probing), §14-4 (scheduler tests), §15 Phase C milestone, §16 Phase C decision rows (added 2026-09-07).

## Global Constraints

- Python 3.12; `uv` for env/deps; no new runtime deps beyond `httpx`; dev dep `respx` only.
- Ruff config (from `pyproject.toml`): select `E,F,I,UP,B,ASYNC`, line-length 100, double quotes, format on. mypy `--strict` runs on `files = ["app"]` only (tests are **not** mypy-scanned). pytest `asyncio_mode = auto`.
- `make gate` = `ruff check .` + `ruff format --check .` + `mypy` + `pytest` — all four must pass at acceptance.
- Do NOT modify existing domain model fields or semantics in `app/domain/models.py`. The only permitted domain edit is adding a read-only `clock` property to `MonitorStateMachine` (Task 2). Do not touch `PLAN.md` §16 rows (already added).
- No filesystem writes outside tmp test files; no real network ever (every httpx call in tests is respx-mocked); no DB.
- `Monitor.interval_s` is an `int` with `ge=1` — scheduler cadence tests therefore use 1s/2s intervals with short real sleeps (see Task 3 test durations); do NOT loosen the field.
- `str(AnyHttpUrl)` yields a trailing slash: any URL that must match a respx route is spelled `"https://example.com/"`.
- Do not push, do not merge, do not touch `main`. Commit per task on the current feature branch and stop after Task 5 acceptance.

### Source-of-truth module shapes (read these before starting — do not recreate)

- `app/domain/clock.py`: `class Clock(Protocol): def now(self) -> datetime: ...` and `class SystemClock: def now(self) -> datetime: return datetime.now(UTC)`.
- `app/domain/models.py`: `CheckType(StrEnum)` (`HTTP = "http"`); `HttpCheckConfig(url: AnyHttpUrl, method: Literal["GET","HEAD","POST","PUT","PATCH","DELETE"] = "GET", expected_status: int = 200, headers: dict[str, str] = Field(default_factory=dict))`; `CheckResult(ok: bool, status_code: int | None = None, latency_ms: float | None = None, error: str | None = None, checked_at: datetime)`; `Monitor(id, name, check_type: CheckType = CheckType.HTTP, check_config: HttpCheckConfig, interval_s: int = Field(default=60, ge=1), timeout_s: float = Field(default=10.0, gt=0), retries: int = 3, cooldown_s: int = 300, channels, enabled: bool = True)`; `MonitorState`, `DomainEvent(id: UUID default uuid4, type: EventType, monitor_id: str, payload: dict, occurred_at: datetime)`, `MonitorFile`.
- `app/domain/state.py`: `MonitorStateMachine(monitor, state: MonitorState | None = None, clock: Clock | None = None)` with `self._clock = clock or SystemClock()` and `def record(self, result: CheckResult) -> list[DomainEvent]`.
- `app/config/loader.py`: `ConfigError(Exception)`; `parse_config(text: str) -> MonitorFile`; `load_config(path: Path) -> MonitorFile` (raises `ConfigError`).
- `app/cli.py`: Typer app with `@app.callback() def main() -> None` + `@app.command("config-check")`; `_print_monitors`/`_print_channels` helpers.
- `tests/conftest.py`: `FakeClock` class + `fake_clock` and `monitor` fixtures (monitor: `id="blog"`, url `https://example.com`).

---

### Task 1: HTTP check strategy

**Files:**
- Create: `app/probe/__init__.py`
- Create: `app/probe/base.py`
- Create: `app/probe/http.py`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `Clock` (`app.domain.clock`), `CheckResult`/`HttpCheckConfig` (`app.domain.models`).
- Produces: `CheckStrategy` protocol with `async def run(self, cfg: HttpCheckConfig, timeout_s: float) -> CheckResult`, and `HTTPCheck` (constructor takes `clock: Clock`) implementing it.

- [ ] **Step 1: Create empty package markers**

```bash
mkdir -p app/probe
touch app/probe/__init__.py
```

- [ ] **Step 2: Write the failing test** — `tests/test_probe.py`

```python
import httpx
import respx

from app.domain.models import HttpCheckConfig
from app.probe.http import HTTPCheck


def make_cfg(
    *,
    method: str = "GET",
    expected_status: int = 200,
    headers: dict[str, str] | None = None,
) -> HttpCheckConfig:
    return HttpCheckConfig(
        url="https://example.com",
        method=method,
        expected_status=expected_status,
        headers=headers or {},
    )


async def test_ok_result(fake_clock) -> None:
    strategy = HTTPCheck(clock=fake_clock)
    cfg = make_cfg()
    with respx.mock:
        respx.get("https://example.com/").mock(return_value=httpx.Response(200, text="ok"))
        result = await strategy.run(cfg, timeout_s=5.0)
    assert result.ok is True
    assert result.status_code == 200
    assert result.error is None
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
    assert result.checked_at == fake_clock.now()


async def test_expected_status_mismatch(fake_clock) -> None:
    strategy = HTTPCheck(clock=fake_clock)
    cfg = make_cfg(expected_status=204)
    with respx.mock:
        respx.get("https://example.com/").mock(return_value=httpx.Response(200))
        result = await strategy.run(cfg, timeout_s=5.0)
    assert result.ok is False
    assert result.status_code == 200
    assert result.error is not None
    assert "expected status 204" in result.error


async def test_custom_headers_are_sent(fake_clock) -> None:
    strategy = HTTPCheck(clock=fake_clock)
    cfg = make_cfg(headers={"X-Token": "secret"})
    with respx.mock:
        respx.get("https://example.com/").mock(return_value=httpx.Response(200))
        await strategy.run(cfg, timeout_s=5.0)
        request = respx.calls.last.request
    assert request.headers["x-token"] == "secret"


async def test_request_method_is_used(fake_clock) -> None:
    strategy = HTTPCheck(clock=fake_clock)
    cfg = make_cfg(method="POST")
    with respx.mock:
        respx.post("https://example.com/").mock(return_value=httpx.Response(204))
        result = await strategy.run(cfg, timeout_s=5.0)
    assert result.ok is True
    assert respx.calls.last.request.method == "POST"


async def test_network_error_is_a_failure(fake_clock) -> None:
    strategy = HTTPCheck(clock=fake_clock)
    cfg = make_cfg()
    with respx.mock:
        respx.get("https://example.com/").mock(side_effect=httpx.ConnectError("connection refused"))
        result = await strategy.run(cfg, timeout_s=5.0)
    assert result.ok is False
    assert result.status_code is None
    assert result.error is not None
    assert "connection refused" in result.error


async def test_timeout_is_a_failure(fake_clock) -> None:
    strategy = HTTPCheck(clock=fake_clock)
    cfg = make_cfg()
    with respx.mock:
        respx.get("https://example.com/").mock(side_effect=httpx.ReadTimeout("read timed out"))
        result = await strategy.run(cfg, timeout_s=5.0)
    assert result.ok is False
    assert result.status_code is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.probe.http'`.

- [ ] **Step 4: Write the minimal implementation** — `app/probe/base.py`

```python
from typing import Protocol

from app.domain.models import CheckResult, HttpCheckConfig


class CheckStrategy(Protocol):
    """One strategy knows how to probe one kind of target (PLAN.md §7)."""

    async def run(self, cfg: HttpCheckConfig, timeout_s: float) -> CheckResult: ...
```

- [ ] **Step 5: Implement `app/probe/http.py`**

```python
from time import perf_counter

import httpx

from app.domain.clock import Clock
from app.domain.models import CheckResult, HttpCheckConfig


class HTTPCheck:
    """Probe an HTTP(S) endpoint via httpx (PLAN.md §7, §13)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def run(self, cfg: HttpCheckConfig, timeout_s: float) -> CheckResult:
        started = perf_counter()
        try:
            timeout = httpx.Timeout(timeout_s)
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                response = await client.request(cfg.method, str(cfg.url), headers=cfg.headers)
        except httpx.HTTPError as exc:
            return CheckResult(ok=False, error=str(exc), checked_at=self._clock.now())
        latency_ms = round((perf_counter() - started) * 1000, 3)
        ok = response.status_code == cfg.expected_status
        error = None if ok else f"expected status {cfg.expected_status}, got {response.status_code}"
        return CheckResult(
            ok=ok,
            status_code=response.status_code,
            latency_ms=latency_ms,
            error=error,
            checked_at=self._clock.now(),
        )
```

Note on `httpx.Timeout(timeout_s)` inside the `try`: keep construction there so a bad timeout still lands in the failure path. Redirects follow; TLS verification is left at httpx defaults (on).

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_probe.py -v`
Expected: PASS (6 passed).

- [ ] **Step 7: Commit**

```bash
git add app/probe tests/test_probe.py
git commit -m "feat: add http check strategy with latency capture"
```

---

### Task 2: Strategy registry + shared `run_check` service

**Files:**
- Create: `app/probe/registry.py`
- Create: `app/service/__init__.py`
- Create: `app/service/checks.py`
- Modify: `app/domain/state.py` (add public `clock` property)
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: `HTTPCheck` (Task 1), `CheckStrategy`, `MonitorStateMachine`/`Clock`.
- Produces: `get_strategy(check_type: CheckType, *, clock: Clock) -> CheckStrategy` (raises `LookupError` for unregistered types) and `async def run_check(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]`.

- [ ] **Step 1: Add the public clock property to the state machine** — edit `app/domain/state.py`, inside class `MonitorStateMachine` (after `__init__`):

```python
    @property
    def clock(self) -> Clock:
        """The single clock source injected into this state machine."""
        return self._clock
```

- [ ] **Step 2: Create `app/probe/registry.py`**

```python
from collections.abc import Callable

from app.domain.clock import Clock
from app.domain.models import CheckType
from app.probe.base import CheckStrategy
from app.probe.http import HTTPCheck

CheckStrategyFactory = Callable[[Clock], CheckStrategy]

_REGISTRY: dict[CheckType, CheckStrategyFactory] = {
    CheckType.HTTP: HTTPCheck,
}


def get_strategy(check_type: CheckType, *, clock: Clock) -> CheckStrategy:
    """Return the registered probe strategy for `check_type` (PLAN.md §7)."""
    factory = _REGISTRY.get(check_type)
    if factory is None:
        raise LookupError(f"no check strategy registered for {check_type!r}")
    return factory(clock)
```

- [ ] **Step 3: Create the shared service** — `app/service/__init__.py` (empty) and `app/service/checks.py`

```python
from app.domain.models import DomainEvent, Monitor
from app.domain.state import MonitorStateMachine
from app.probe.registry import get_strategy


async def run_check(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]:
    """Probe `monitor` then feed the result through `machine` (D8 shared path)."""
    strategy = get_strategy(monitor.check_type, clock=machine.clock)
    result = await strategy.run(monitor.check_config, monitor.timeout_s)
    return machine.record(result)
```

- [ ] **Step 4: Write the failing test** — `tests/test_checks.py`

```python
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
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `uv run pytest tests/test_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.probe.registry'`.

- [ ] **Step 6: Implement** (Steps 1–3 above), then run the tests again.

Run: `uv run pytest tests/test_checks.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest`
Expected: all prior + new tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/probe/registry.py app/service tests/test_checks.py app/domain/state.py
git commit -m "feat: add check strategy registry and shared run_check service"
```

---

### Task 3: Async scheduler

**Files:**
- Create: `app/scheduler/__init__.py`
- Create: `app/scheduler/loop.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `run_check` (Task 2), `Monitor`/`DomainEvent`/`MonitorState`, `MonitorStateMachine`, `Clock`/`SystemClock`.
- Produces: `Scheduler(monitors: Sequence[Monitor], *, ticker: CheckTicker | None = None, clock: Clock | None = None, max_concurrency: int = 5, jitter: bool = True, rng: random.Random | None = None)` with `async def start(self) -> None`, `async def stop(self) -> None`; module type alias `CheckTicker = Callable[[Monitor, MonitorStateMachine], Awaitable[list[DomainEvent]]]`.

- [ ] **Step 1: Write the failing test** — `tests/test_scheduler.py`

```python
import asyncio
import time
from collections import defaultdict

from app.domain.clock import SystemClock
from app.domain.models import DomainEvent, HttpCheckConfig, Monitor, MonitorState
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scheduler.loop'`.

- [ ] **Step 3: Create the scheduler** — `app/scheduler/__init__.py` (empty) and `app/scheduler/loop.py`

```python
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
```

Notes: `asyncio.CancelledError` is a `BaseException` subclass, so `except Exception` never swallows task cancellation — `stop()` works. A raising ticker is logged and the per-monitor task continues (isolation). Fixed-phase `due += interval` keeps cadence even if a tick runs long.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS (5 passed). Total run time ~4s.

- [ ] **Step 5: Commit**

```bash
git add app/scheduler tests/test_scheduler.py
git commit -m "feat: add async per-monitor scheduler with isolation and concurrency cap"
```

---

### Task 4: `wm check <id>` CLI

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli_check.py`

**Interfaces:**
- Consumes: `load_config`/`ConfigError`, `run_check` (Task 2), `MonitorStateMachine`, models `Monitor`/`MonitorFile`/`CheckResult`.
- Produces: `@app.command("check") def check_command(monitor_id: str, config: Path = DEFAULT_CONFIG_PATH) -> None` (exit 0 on a completed probe, exit 1 on config/load/id errors) plus private helpers `_find_monitor` and `_print_check_result`. Module constant `DEFAULT_CONFIG_PATH = Path("config/monitors.yaml")` shared with `config-check`.

- [ ] **Step 1: Refactor the shared default path** — edit `app/cli.py` so the module now starts:

```python
from pathlib import Path
from typing import Annotated

import typer

from app.config.loader import ConfigError, load_config
from app.domain.models import CheckResult, Monitor, MonitorFile
from app.domain.state import MonitorStateMachine
from app.service.checks import run_check

app = typer.Typer(help="web-monitor command line interface")

DEFAULT_CONFIG_PATH = Path("config/monitors.yaml")
```

and `config_check`'s `config_path` default becomes `DEFAULT_CONFIG_PATH` (delete the duplicated `Path("config/monitors.yaml")` literal).

- [ ] **Step 2: Write the failing test** — `tests/test_cli_check.py`

```python
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from app.cli import app
from app.domain.models import CheckResult

runner = CliRunner()

VALID_CONFIG = (
    "monitors:\n  - id: blog\n    name: Blog\n    check_config:\n      url: https://example.com\n"
)


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config" / "monitors.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(VALID_CONFIG, encoding="utf-8")
    return path


def make_result(*, ok: bool, error: str | None = None) -> CheckResult:
    return CheckResult(ok=ok, error=error, checked_at=datetime.now(UTC))


def test_check_ok(monkeypatch, tmp_path) -> None:
    async def fake_run(monitor, machine):
        machine.state.last_result = make_result(ok=True)
        return []

    monkeypatch.setattr("app.cli.run_check", fake_run)
    result = runner.invoke(app, ["check", "blog", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 0
    assert "check ok" in result.stdout


def test_check_failed_prints_failure(monkeypatch, tmp_path) -> None:
    async def fake_run(monitor, machine):
        machine.state.last_result = make_result(ok=False, error="connection refused")
        return []

    monkeypatch.setattr("app.cli.run_check", fake_run)
    result = runner.invoke(app, ["check", "blog", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 0
    assert "check failed: connection refused" in result.stdout


def test_check_unknown_monitor(tmp_path) -> None:
    result = runner.invoke(app, ["check", "nope", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 1
    assert "no monitor with id 'nope'" in result.stderr


def test_check_missing_config(tmp_path) -> None:
    result = runner.invoke(app, ["check", "blog", "--config", str(tmp_path / "none.yaml")])
    assert result.exit_code == 1
    assert "config file not found" in result.stderr
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli_check.py -v`
Expected: FAIL (no `check` command — exit code 2).

- [ ] **Step 4: Implement** — append to `app/cli.py` (before the existing `_print_monitors` helper):

```python
@app.command("check")
def check_command(
    monitor_id: Annotated[str, typer.Argument(help="id of the monitor to probe now")],
    config: Annotated[Path, typer.Option("--config", help="path to monitors.yaml")] = (
        DEFAULT_CONFIG_PATH
    ),
) -> None:
    try:
        config_file = load_config(config)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    monitor = _find_monitor(config_file, monitor_id)
    if monitor is None:
        typer.secho(f"no monitor with id '{monitor_id}' in {config}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    machine = MonitorStateMachine(monitor)
    events = asyncio.run(run_check(monitor, machine))
    _print_check_result(machine.state.last_result)
    for event in events:
        typer.echo(f"- event {event.type.value} at {event.occurred_at.isoformat()}")


def _find_monitor(config_file: MonitorFile, monitor_id: str) -> Monitor | None:
    return next((item for item in config_file.monitors if item.id == monitor_id), None)


def _print_check_result(result: CheckResult | None) -> None:
    if result is None:
        return
    if result.ok:
        status = result.status_code if result.status_code is not None else "-"
        latency = f"{result.latency_ms:.3f}ms" if result.latency_ms is not None else "-"
        typer.secho(f"check ok: {status} in {latency}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"check failed: {result.error}", fg=typer.colors.RED)
```

Add `import asyncio` at the top of `app/cli.py`. A completed probe exits 0 whether the target is up or down; only operator errors (missing config/monitor) exit 1. `asyncio.run` is safe because Typer commands run in a sync context.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli_check.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: all pass (prior 41 + 6 probe + 4 checks + 5 scheduler + 4 cli-check = 60).

- [ ] **Step 7: Commit**

```bash
git add app/cli.py tests/test_cli_check.py
git commit -m "feat: add wm check command sharing the scheduler code path"
```

---

### Task 5: Dependency moves + acceptance

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Modify: `PLAN.md` (§15 Phase C milestone mark)

**Interfaces:**
- Consumes: everything from Tasks 1–4.

- [ ] **Step 1: Move `httpx` to runtime deps; add `respx` to dev** — edit `pyproject.toml`

Move `httpx>=0.27` out of `[dependency-groups] dev` and into `[project] dependencies` (e.g. alphabetically next to the others, keeping `httpx>=0.27`). Add `respx>=0.21` to the dev group. Leave every other dependency untouched.

- [ ] **Step 2: Lock**

Run: `uv lock && uv sync --frozen`
Expected: success; `uv.lock` updated (httpx now a runtime dependency).

- [ ] **Step 3: Run the full gate**

Run: `make gate`
Expected: all four steps green — `ruff check .`, `ruff format --check .`, `mypy` (Success on app sources), `pytest` (60 passed).

If `ruff format --check .` reports the Python fences inside the new plan doc or `PLAN.md` as unformatted, run `uv run ruff format PLAN.md docs/superpowers/plans/2026-09-07-phase-c-probing-scheduler.md` and re-run `make gate`.

- [ ] **Step 4: Mark the milestone done** — edit `PLAN.md` §15. Above the line `**Phase C — Probing + scheduler**` insert the same marker used for earlier phases:

```markdown
> ✅ Done (see `docs/superpowers/plans/2026-09-07-phase-c-probing-scheduler.md`). **Phase C — Probing + scheduler**
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock PLAN.md docs/superpowers/plans/2026-09-07-phase-c-probing-scheduler.md
git commit -m "docs: mark phase c probing + scheduler complete"
```

- [ ] **Step 6: Report and STOP**

Report to the parent session: branch name, commit hashes (short), gate summary (counts per check), pytest totals, and confirm no pushes/merges were made. Do not merge, tag, or push.
