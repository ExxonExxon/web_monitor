# Phase B — Domain Core (no I/O) Implementation Plan

> **For agentic workers:** this plan is executed by a single fresh subagent, task by task, in order. Steps use checkbox (`- [ ]`) syntax. Work happens on branch `feature/phase-b-domain-core` created from `develop`. Commit after every task. Do NOT push, do NOT merge, do NOT touch `main`. Run the full gate (`make gate`) at the end of Task 5 and report results.

**Goal:** Build the typed domain core of web-monitor with zero network and zero database I/O: Pydantic domain models, the confirm-before-alert state machine, the YAML monitor-config loader/validator, and the `wm config-check` CLI.

**Architecture:** A pure `app/domain` package (models, clock, state machine) that knows nothing about transport, storage, or scheduling; an `app/config` loader that validates `config/monitors.yaml` into the domain models; and a thin Typer CLI in `app/cli.py` exposing `config-check`. The state machine is a plain object that mutates a `MonitorState` and returns `list[DomainEvent]`.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, Typer. New runtime deps this phase: `pyyaml`, `typer` (+ dev `types-PyYAML`).

**Spec:** `PLAN.md` §5 (Domain Model), §9 (Configuration), §11 (CLI), §14 (Testing). Phase B milestone: §15. Locked Phase B decisions: §16 rows (added 2026-09-07).

## Global Constraints

- Python `>=3.12`, managed by `uv`. Never touch the global system Python.
- Ruff: line-length 100, target py312, selected `E,F,I,UP,B,ASYNC`, double quotes.
- mypy `--strict` over the `app` package only (tests are not type-checked).
- pytest with `testpaths = tests`.
- New domain code must import nothing outside: `app.domain.*`, `app.config.*`, stdlib, `pydantic`, `yaml`, `typer`.
- No network calls, no filesystem writes outside tmp test files, no database — Phase B gate says so.
- The state machine is the highest-value suite in the repo: table-driven, deterministic, injected clock. Cover every scenario listed in PLAN.md §14.1.
- No comments that restate code. Docstrings allowed on public classes.
- Rule: an internal state machine rule the tests must encode — a DOWN alert is emitted only when the machine transitions to DOWN and the cooldown window since the *last emitted DOWN alert* has elapsed. RECOVERED is always emitted on a DOWN→UP transition.

---

### Task 1: Dependencies + domain models + clock

**Files:**
- Modify: `pyproject.toml`
- Create: `app/domain/__init__.py`, `app/config/__init__.py` (empty markers)
- Create: `app/domain/models.py`
- Create: `app/domain/clock.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: Phase A scaffold (`pyproject.toml`, ruff/mypy/pytest config).
- Produces: `app.domain.models` exports `CheckType, Status, EventType, ChannelType, HttpCheckConfig, CheckResult, Monitor, MonitorState, DomainEvent, ChannelConfig, MonitorFile`. `app.domain.clock` exports `Clock` (Protocol) and `SystemClock`. Later tasks (state machine, loader, CLI) depend on these exact names.

- [ ] **Step 1: Add dependencies and console script to `pyproject.toml`**

In the `[project]` table add to `dependencies`:

```toml
"pyyaml>=6.0.2",
"typer>=0.12",
```

After `[project]` add the console script (this turns `app.cli:app` into the `wm` command; it is wired in Task 4, do not import `app.cli` yet):

```toml
[project.scripts]
wm = "app.cli:app"
```

In the `[dependency-groups]` dev group add:

```toml
"types-PyYAML>=6.0",
```

- [ ] **Step 2: Create the empty package markers**

`app/domain/__init__.py` and `app/config/__init__.py` — empty files.

- [ ] **Step 3: Write the failing model tests** — `tests/test_models.py`

```python
import uuid

import pytest
from pydantic import ValidationError

from app.domain.models import (
    CheckResult,
    ChannelConfig,
    DomainEvent,
    EventType,
    HttpCheckConfig,
    Monitor,
    MonitorFile,
    MonitorState,
    Status,
)


def make_monitor(**overrides: object) -> Monitor:
    fields: dict[str, object] = {
        "id": "blog",
        "name": "Blog",
        "check_config": HttpCheckConfig(url="https://example.com"),
    }
    fields.update(overrides)
    return Monitor.model_validate(fields)


def test_monitor_defaults() -> None:
    monitor = make_monitor()
    assert monitor.check_type.value == "http"
    assert monitor.interval_s == 60
    assert monitor.timeout_s == 10.0
    assert monitor.retries == 3
    assert monitor.cooldown_s == 300
    assert monitor.channels is None
    assert monitor.enabled is True


def test_monitor_id_must_be_slug() -> None:
    with pytest.raises(ValidationError):
        make_monitor(id="My Blog")


def test_monitor_requires_valid_url() -> None:
    with pytest.raises(ValidationError):
        Monitor.model_validate(
            {"id": "blog", "name": "Blog", "check_config": {"url": "not-a-url"}}
        )


def test_monitor_state_defaults() -> None:
    state = MonitorState(monitor_id="blog")
    assert state.current_status == Status.UNKNOWN
    assert state.consecutive_failures == 0
    assert state.last_check_at is None
    assert state.last_result is None
    assert state.down_since is None
    assert state.last_alert_at is None


def test_check_result_defaults() -> None:
    result = CheckResult(ok=False)
    assert result.status_code is None
    assert result.latency_ms is None
    assert result.error is None


def test_domain_event_has_uuid_and_timestamp() -> None:
    event = DomainEvent(type=EventType.SERVICE_DOWN, monitor_id="blog", occurred_at=result_ts())
    assert isinstance(event.id, uuid.UUID)
    assert event.payload == {}
    assert event.occurred_at.year == 2026


def result_ts():
    from datetime import datetime, timezone

    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_channel_events_default_to_down_and_recovered() -> None:
    channel = ChannelConfig(name="home", type="telegram")
    assert channel.events == [EventType.SERVICE_DOWN, EventType.SERVICE_RECOVERED]


def test_channel_explicit_events_preserved() -> None:
    channel = ChannelConfig(name="home", type="telegram", events=[EventType.SERVICE_RECOVERED])
    assert channel.events == [EventType.SERVICE_RECOVERED]


def test_monitor_file_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        MonitorFile.model_validate({"monitors": [], "bogus": 1})


def test_monitor_file_rejects_duplicate_monitor_ids() -> None:
    payload = {"monitors": [make_monitor().model_dump(), make_monitor(id="blog").model_dump()]}
    with pytest.raises(ValidationError, match="duplicate monitor ids"):
        MonitorFile.model_validate(payload)


def test_monitor_file_rejects_duplicate_channel_names() -> None:
    payload = {
        "channels": [
            {"name": "home", "type": "telegram"},
            {"name": "home", "type": "telegram"},
        ]
    }
    with pytest.raises(ValidationError, match="duplicate channel names"):
        MonitorFile.model_validate(payload)
```

Note: `test_domain_event_has_uuid_and_timestamp` uses a module helper `result_ts()` declared below it — Python resolves names at call time, this is fine. But move `result_ts` to the top for readability if you prefer; ruff will not reorder your code.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.models'`.

- [ ] **Step 5: Implement the domain models** — `app/domain/models.py`

```python
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class CheckType(str, Enum):
    HTTP = "http"


class Status(str, Enum):
    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"
    PAUSED = "paused"


class EventType(str, Enum):
    SERVICE_DOWN = "service_down"
    SERVICE_RECOVERED = "service_recovered"


class ChannelType(str, Enum):
    TELEGRAM = "telegram"


class HttpCheckConfig(BaseModel):
    url: AnyHttpUrl
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    expected_status: int = 200
    headers: dict[str, str] = Field(default_factory=dict)


class CheckResult(BaseModel):
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    checked_at: datetime


class Monitor(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
    name: str
    check_type: CheckType = CheckType.HTTP
    check_config: HttpCheckConfig
    interval_s: int = Field(default=60, ge=1)
    timeout_s: float = Field(default=10.0, gt=0)
    retries: int = Field(default=3, ge=1)
    cooldown_s: int = Field(default=300, ge=0)
    channels: list[str] | None = None
    enabled: bool = True


class MonitorState(BaseModel):
    monitor_id: str
    current_status: Status = Status.UNKNOWN
    consecutive_failures: int = 0
    last_check_at: datetime | None = None
    last_result: CheckResult | None = None
    down_since: datetime | None = None
    last_alert_at: datetime | None = None


class DomainEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    type: EventType
    monitor_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class ChannelConfig(BaseModel):
    name: str
    type: ChannelType
    settings: dict[str, Any] = Field(default_factory=dict)
    events: list[EventType] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_events(self) -> ChannelConfig:
        if not self.events:
            self.events = [EventType.SERVICE_DOWN, EventType.SERVICE_RECOVERED]
        return self


class MonitorFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitors: list[Monitor] = Field(default_factory=list)
    channels: list[ChannelConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> MonitorFile:
        monitor_ids = [item.id for item in self.monitors]
        duplicates = {mid for mid in monitor_ids if monitor_ids.count(mid) > 1}
        if duplicates:
            raise ValueError(f"duplicate monitor ids: {', '.join(sorted(duplicates))}")
        channel_names = [item.name for item in self.channels]
        dup_names = {name for name in channel_names if channel_names.count(name) > 1}
        if dup_names:
            raise ValueError(f"duplicate channel names: {', '.join(sorted(dup_names))}")
        return self
```

- [ ] **Step 6: Implement the clock** — `app/domain/clock.py`

```python
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
```

- [ ] **Step 7: Run the model tests**

Run: `uv run pytest tests/test_models.py -q`
Expected: PASS (10 passed).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock app/domain app/config tests/test_models.py
git commit -m "feat: add domain models, clock, and pydantic config types"
```

---

### Task 2: State machine (the high-value suite)

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/test_state.py`
- Create: `app/domain/state.py`

**Interfaces:**
- Consumes: `app.domain.models.*`, `app.domain.clock.Clock/SystemClock` from Task 1.
- Produces: `app.domain.state.MonitorStateMachine` — constructor `(monitor: Monitor, state: MonitorState | None = None, clock: Clock | None = None)` and method `record(self, result: CheckResult) -> list[DomainEvent]`. Later phases (scheduler, dispatcher) call `record` exactly this way. State is mutated in place on `self.state`.

- [ ] **Step 1: Write shared test fixtures** — `tests/conftest.py`

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models import HttpCheckConfig, Monitor


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def monitor() -> Monitor:
    return Monitor(id="blog", name="Blog", check_config=HttpCheckConfig(url="https://example.com"))
```

- [ ] **Step 2: Write the failing state machine tests** — `tests/test_state.py`

```python
from datetime import datetime, timezone

from app.domain.models import (
    CheckResult,
    DomainEvent,
    EventType,
    HttpCheckConfig,
    Monitor,
    MonitorState,
    Status,
)
from app.domain.state import MonitorStateMachine


def _ok(clock, *, latency_ms: float | None = None, status_code: int = 200) -> CheckResult:
    return CheckResult(ok=True, status_code=status_code, latency_ms=latency_ms, checked_at=clock.now())


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
    events = machine.record(_fail(fake_clock))
    assert events == []
    events = machine.record(_fail(fake_clock))
    assert events == []
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
    assert payload["url"] == "https://example.com"
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
    assert payload["duration_down_s"] == 40.0
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
    assert machine.state.last_alert_at.year == 2026
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
```

Unused import check: `DomainEvent` and `datetime`/`timezone` are unused in the test above — ruff will flag them. Remove `from datetime import datetime, timezone` and drop `DomainEvent` from the import list before running.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_state.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.state'`.

- [ ] **Step 4: Implement the state machine** — `app/domain/state.py`

```python
from datetime import datetime
from typing import Any

from app.domain.clock import Clock, SystemClock
from app.domain.models import (
    CheckResult,
    DomainEvent,
    EventType,
    Monitor,
    MonitorState,
    Status,
)


class MonitorStateMachine:
    def __init__(
        self,
        monitor: Monitor,
        state: MonitorState | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.monitor = monitor
        self.state = state or MonitorState(monitor_id=monitor.id)
        self._clock = clock or SystemClock()

    def record(self, result: CheckResult) -> list[DomainEvent]:
        now = self._clock.now()
        self.state.last_check_at = now
        self.state.last_result = result
        if self.state.current_status == Status.PAUSED:
            return []
        if result.ok:
            self.state.consecutive_failures = 0
            if self.state.current_status == Status.DOWN:
                return self._recover(now, result)
            self.state.current_status = Status.UP
            return []
        self.state.consecutive_failures += 1
        if self.state.current_status == Status.DOWN:
            return []
        if self.state.consecutive_failures < self.monitor.retries:
            return []
        self.state.current_status = Status.DOWN
        if self.state.down_since is None:
            self.state.down_since = now
        if self._within_cooldown(now):
            return []
        self.state.last_alert_at = now
        return [self._build_event(EventType.SERVICE_DOWN, now, result)]

    def _within_cooldown(self, now: datetime) -> bool:
        if self.state.last_alert_at is None:
            return False
        elapsed = (now - self.state.last_alert_at).total_seconds()
        return elapsed < self.monitor.cooldown_s

    def _recover(self, now: datetime, result: CheckResult) -> list[DomainEvent]:
        duration: float | None = None
        if self.state.down_since is not None:
            duration = round((now - self.state.down_since).total_seconds(), 3)
        self.state.current_status = Status.UP
        self.state.down_since = None
        return [self._build_event(EventType.SERVICE_RECOVERED, now, result, duration)]

    def _build_event(
        self,
        event_type: EventType,
        now: datetime,
        result: CheckResult,
        duration_down_s: float | None = None,
    ) -> DomainEvent:
        payload: dict[str, Any] = {
            "name": self.monitor.name,
            "url": str(self.monitor.check_config.url),
            "latency_ms": result.latency_ms,
        }
        if result.error is not None:
            payload["error"] = result.error
        if result.status_code is not None:
            payload["status_code"] = result.status_code
        if event_type is EventType.SERVICE_RECOVERED:
            if duration_down_s is not None:
                payload["duration_down_s"] = duration_down_s
            payload["resolved_at"] = now.isoformat()
        return DomainEvent(type=event_type, monitor_id=self.monitor.id, payload=payload, occurred_at=now)
```

- [ ] **Step 5: Run the state machine tests**

Run: `uv run pytest tests/test_state.py -q`
Expected: PASS. If a semantic test disagrees with you, the TEST is the spec — do not weaken it; re-check the rule in Global Constraints.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (all previous Phase A tests plus new ones).

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_state.py app/domain/state.py
git commit -m "feat: add confirm-before-alert state machine with cooldown"
```

---

### Task 3: YAML monitor config loader

**Files:**
- Create: `app/config/loader.py`
- Test: `tests/test_loader.py`
- Create: `config/monitors.yaml.example`

**Interfaces:**
- Consumes: `app.domain.models.MonitorFile` from Task 1.
- Produces: `app.config.loader` exports `ConfigError(Exception)` and two functions: `parse_config(text: str) -> MonitorFile` and `load_config(path: Path) -> MonitorFile`. Task 4's CLI consumes both.

- [ ] **Step 1: Write the failing loader tests** — `tests/test_loader.py`

```python
from pathlib import Path

import pytest

from app.config.loader import ConfigError, load_config, parse_config

VALID_YAML = """
monitors:
  - id: my-blog
    name: My Blog
    check_config:
      url: https://blog.example.com
      expected_status: 200
    retries: 5
channels:
  - name: telegram-home
    type: telegram
    events: [service_down]
"""


def test_parse_config_loads_monitors_and_channels() -> None:
    config = parse_config(VALID_YAML)
    assert len(config.monitors) == 1
    monitor = config.monitors[0]
    assert monitor.id == "my-blog"
    assert monitor.retries == 5
    assert monitor.interval_s == 60
    assert str(monitor.check_config.url) == "https://blog.example.com/"
    assert config.channels[0].events == ["service_down"]


def test_parse_config_accepts_empty_document() -> None:
    config = parse_config("")
    assert config.monitors == []
    assert config.channels == []


def test_parse_config_rejects_non_mapping_root() -> None:
    with pytest.raises(ConfigError, match="root must be a mapping"):
        parse_config("- just\n- a\n- list")


def test_parse_config_reports_yaml_syntax_error() -> None:
    with pytest.raises(ConfigError, match="invalid YAML"):
        parse_config("monitors: [unclosed")


def test_parse_config_reports_validation_issues() -> None:
    with pytest.raises(ConfigError, match="invalid config"):
        parse_config("monitors:\n  - id: one\n    check_config:\n      url: nope\n")


def test_parse_config_rejects_duplicate_ids() -> None:
    text = """
monitors:
  - id: dup
    name: One
    check_config:
      url: https://a.example.com
  - id: dup
    name: Two
    check_config:
      url: https://b.example.com
"""
    with pytest.raises(ConfigError, match="duplicate monitor ids"):
        parse_config(text)


def test_parse_config_rejects_unknown_check_type() -> None:
    text = """
monitors:
  - id: thing
    name: Thing
    check_type: tcp
    check_config:
      url: https://a.example.com
"""
    with pytest.raises(ConfigError):
        parse_config(text)


def test_load_config_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    config = load_config(path)
    assert config.monitors[0].id == "my-blog"


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")
```

Note: `monitor.check_config.url` is an `AnyHttpUrl`; comparing `str(...)` ends with `/` for a bare host. The assertion above uses the normalized string. If `AnyHttpUrl` keeps a trailing slash that surprises you, keep the assertion as written — it is the observed normalization.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loader.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config.loader'`.

- [ ] **Step 3: Implement the loader** — `app/config/loader.py`

```python
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain.models import MonitorFile


class ConfigError(Exception):
    """Raised when the monitor config file cannot be read, parsed, or validated."""


def parse_config(text: str) -> MonitorFile:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping, got {type(data).__name__}")
    try:
        return MonitorFile.model_validate(data)
    except ValidationError as exc:
        issues = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            issues.append(f"- {location}: {error['msg']}")
        raise ConfigError(f"invalid config:\n" + "\n".join(issues)) from exc


def load_config(path: Path) -> MonitorFile:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse_config(text)
```

- [ ] **Step 4: Run the loader tests**

Run: `uv run pytest tests/test_loader.py -q`
Expected: PASS. If `str(url)` normalization differs, fix the assertion, not the code.

- [ ] **Step 5: Add the commented example config** — `config/monitors.yaml.example`

```yaml
# web-monitor configuration example.
# Copy to config/monitors.yaml (the path `wm config-check` validates by default).
monitors:
  - id: my-blog
    name: My Blog
    check_type: http
    check_config:
      url: https://blog.example.com
      method: GET
      expected_status: 200
      headers: {}
    interval_s: 60
    timeout_s: 10
    retries: 3
    cooldown_s: 300
    enabled: true
channels:
  - name: telegram-home
    type: telegram
    events: [service_down, service_recovered]
```

- [ ] **Step 6: Commit**

```bash
git add app/config/loader.py tests/test_loader.py config/monitors.yaml.example
git commit -m "feat: add yaml monitor config loader and validator"
```

---

### Task 4: `wm config-check` CLI

**Files:**
- Create: `app/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `app.config.loader.load_config/ConfigError`, `app.domain.models.MonitorFile` from Tasks 1 and 3.
- Produces: `app.cli.app` — a Typer app. Registered as `wm` via `[project.scripts]` (Task 1). Command: `wm config-check [PATH]`.

- [ ] **Step 1: Write the failing CLI tests** — `tests/test_cli.py`

```python
from pathlib import Path

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()

VALID_YAML = """
monitors:
  - id: my-blog
    name: My Blog
    check_config:
      url: https://blog.example.com
channels:
  - name: telegram-home
    type: telegram
"""


def test_config_check_reports_ok_on_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 0
    assert "config OK" in result.stdout
    assert "my-blog" in result.stdout
    assert "telegram-home" in result.stdout


def test_config_check_fails_on_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nope.yaml"
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_config_check_fails_on_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text("monitors: [unclosed", encoding="utf-8")
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 1
    assert "invalid YAML" in result.stderr


def test_config_check_fails_on_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text("monitors:\n  - id: one\n    check_config:\n      url: nope\n", encoding="utf-8")
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 1
    assert "invalid config" in result.stderr


def test_config_check_default_path_is_config_monitors_yaml(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "monitors.yaml"
    target.write_text(VALID_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config-check"])
    assert result.exit_code == 0
    assert "config OK" in result.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.cli'`.

- [ ] **Step 3: Implement the CLI** — `app/cli.py`

```python
from pathlib import Path
from typing import Annotated

import typer

from app.config.loader import ConfigError, load_config
from app.domain.models import MonitorFile

app = typer.Typer(help="web-monitor command line interface")


@app.command("config-check")
def config_check(
    config_path: Annotated[Path, typer.Argument(help="path to monitors.yaml")] = Path(
        "config/monitors.yaml"
    ),
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(f"config OK: {config_path}", fg=typer.colors.GREEN)
    _print_monitors(config)
    _print_channels(config)


def _print_monitors(config: MonitorFile) -> None:
    for monitor in config.monitors:
        cadence = "disabled" if not monitor.enabled else f"every {monitor.interval_s}s"
        url = str(monitor.check_config.url)
        typer.echo(
            f"- {monitor.id} ({monitor.name}): {monitor.check_type.value} {url} "
            f"[{cadence}, timeout {monitor.timeout_s}s, retries {monitor.retries}, "
            f"cooldown {monitor.cooldown_s}s]"
        )


def _print_channels(config: MonitorFile) -> None:
    for channel in config.channels:
        events = ", ".join(event.value for event in channel.events)
        typer.echo(f"- channel {channel.name}: {channel.type.value} -> [{events}]")
```

- [ ] **Step 4: Run the CLI tests**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS. If the default-path test fails because monkeypatch fixture is untyped, that is fine — tests are not mypy-scanned.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "feat: add wm config-check cli command"
```

---

### Task 5: Phase B acceptance

- [ ] **Step 1: Fresh gate**

Run: `uv sync --frozen && make gate`
Expected: ruff check OK, ruff format --check OK, mypy success, pytest all green.

If `ruff format --check` fails only on Python code fences inside markdown files (this repo's `PLAN.md` or this plan document), reformat those files: `uv run ruff format PLAN.md docs/superpowers/plans/2026-09-07-phase-b-domain-core.md` and commit the reformat as part of this task. Do not touch the formatting config.

If mypy complains about the Typer usage in `app/cli.py` (strict-mode false positives are known on some Typer versions), fix it by typing the code correctly first; only if a genuine Typer typing bug prevents a strict pass, add a narrowly-scoped `# type: ignore[arg-type]` (or exact code) on that single line with the reason already evident from context — never a blanket file-level ignore.

- [ ] **Step 2: Manual smoke of the CLI**

Run: `uv run wm --help` — confirm it lists `config-check`.
Run: `uv run wm config-check config/monitors.yaml.example` — expect exit 0, green `config OK` line, and the sample monitor + channel printed.

- [ ] **Step 3: Mark the milestone done in PLAN.md**

Edit `PLAN.md`, in §15, the line above the Phase B bullet that reads:

```markdown
**Phase B — Domain core (no I/O)**
```

Change it to:

```markdown
> ✅ Done (see `docs/superpowers/plans/2026-09-07-phase-b-domain-core.md`). **Phase B — Domain core (no I/O)**
```

- [ ] **Step 4: Final gate**

Run: `make gate`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add PLAN.md
git commit -m "docs: mark phase b domain core complete"
```

- [ ] **Step 6: Report back**

Do NOT push. Do NOT merge. Do NOT touch `main`. Report: final commit list (`git log --oneline develop..HEAD`), gate output summary, and any deviation from this plan.
