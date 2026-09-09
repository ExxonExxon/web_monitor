# Phase D — Persistence + Outbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist monitor state, check history, and a durable notification outbox to SQLite (WAL), with repositories, state rehydration, retention pruning, and a crash-resume dispatcher.

**Architecture:** A thin `Database` wrapper over `aiosqlite` owns the connection, WAL pragmas, and schema init. Repositories translate between Pydantic domain objects and rows. A `Store` facade bundles the repositories and exposes the write path (`record_check`), boot rehydration, and pruning. The dispatcher reads/writes delivery state in SQLite so retries survive process restarts; transport is injected as a `DeliveryHandler` callable (Phase E supplies the Envelope + provider handler).

**Tech Stack:** Python 3.12, `aiosqlite`, Pydantic v2, pytest + pytest-asyncio, `uv`.

**Spec:** `PLAN.md` §5.1 (storage layout), §6.2–§6.4 (outbox guarantees), §8 (reliability), §14 (testing), §15 (Phase D milestone).

## Global Constraints

- Python 3.12, `uv` for all dependency/env operations. Never edit `uv.lock` by hand.
- Gate = `uv sync --frozen && make gate` (ruff check + `ruff format --check .` + mypy strict + pytest). All four must pass before a task is done.
- ruff: line-length 100, target py312, select `E,F,I,UP,B,ASYNC`, double quotes. mypy strict is scoped to `app/` only (tests are NOT type-checked).
- Do NOT modify existing domain model fields or the state-machine/check/probe/scheduler semantics. Phase D is additive.
- Timestamps: timezone-aware UTC. Persist as ISO-8601 text. All `updated_at`/`created_at`/`next_attempt_at` bookkeeping uses the injected `Clock`, never `datetime.now()`.
- JSON columns use Pydantic `model_dump_json()` / `model_validate_json()`.
- Tests use a tmp DB file and the `FakeClock` fixture. No real network, no real `data/` writes.
- Runtime wiring (app lifespan: open DB, start scheduler/dispatcher) is **deferred to Phase E**. Do NOT edit `app/main.py` in this phase. Phase D ships tested components only.
- `ruff format --check .` also formats Python fences inside markdown. If it flags `PLAN.md` or this plan doc, run `uv run ruff format PLAN.md docs/superpowers/plans/2026-09-07-phase-d-persistence-outbox.md` and fold it into the current task's commit.
- Executor: commit per task on the current feature branch. Do NOT push, merge, or touch `main`. STOP after Task 5 and report.

## Module shape reference (exact, do not re-derive)

- `app/domain/models.py`: `CheckType(StrEnum).HTTP`, `Status(StrEnum)` UNKNOWN/UP/DOWN/PAUSED, `EventType(StrEnum)` SERVICE_DOWN/SERVICE_RECOVERED, `ChannelType(StrEnum).TELEGRAM`. `HttpCheckConfig(url: AnyHttpUrl, method: Literal[...]="GET", expected_status=200, headers={})`. `CheckResult(ok: bool, status_code: int|None, latency_ms: float|None, error: str|None, checked_at: datetime)`. `Monitor(id: str, name, check_type=HTTP, check_config, interval_s=60, timeout_s=10.0, retries=3, cooldown_s=300, channels: list[str]|None=None, enabled=True)`. `MonitorState(monitor_id, current_status=UNKNOWN, consecutive_failures=0, last_check_at=None, last_result=None, down_since=None, last_alert_at=None)`. `DomainEvent(id: UUID, type, monitor_id, payload: dict, occurred_at)`. `ChannelConfig(name, type, settings: dict, events: list[EventType])` (empty events defaults to both event types).
- `app/domain/clock.py`: `class Clock(Protocol): def now(self) -> datetime: ...`; `SystemClock.now()` -> `datetime.now(UTC)`.
- `app/core/config.py`: `Settings(BaseSettings)` with `SettingsConfigDict(env_file=".env", env_prefix="WM_", extra="ignore")`; fields `app_name`, `log_level`, `timezone`.
- `tests/conftest.py`: `FakeClock(start=None)` with `.now()` / `.advance(seconds)`; fixtures `fake_clock`, `monitor` (id="blog", url `https://example.com`).

---

### Task 1: Database, schema, and settings

**Files:**
- Create: `app/persistence/__init__.py`, `app/persistence/schema.sql`, `app/persistence/db.py`
- Modify: `app/core/config.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `app.domain.clock.SystemClock`.
- Produces: `Database(path)`, `await Database.connect()`, `await Database.close()`, `Database.connection -> aiosqlite.Connection`, `Database.transaction()` async context manager, `await Database.execute(sql, params=())`, `await Database.fetchone(sql, params=()) -> aiosqlite.Row | None`, `await Database.fetchall(sql, params=()) -> list[aiosqlite.Row]`. `Settings.db_path: Path`, `Settings.retention_days: int`.

- [ ] **Step 1: Add the runtime dependency**

Run: `uv add "aiosqlite>=0.20"`
Expected: `pyproject.toml` `[project].dependencies` gains `aiosqlite>=0.20`; `uv.lock` updates.

- [ ] **Step 2: Write the schema**

`app/persistence/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS monitor_state (
    monitor_id TEXT PRIMARY KEY,
    current_status TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_check_at TEXT,
    last_result TEXT,
    down_since TEXT,
    last_alert_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    status_code INTEGER,
    latency_ms REAL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_checks_monitor_checked
    ON checks (monitor_id, checked_at);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    monitor_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id INTEGER NOT NULL REFERENCES outbox(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (outbox_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_status_next
    ON deliveries (status, next_attempt_at);
```

- [ ] **Step 3: Write `app/persistence/__init__.py`** (empty file)

- [ ] **Step 4: Write the failing test**

`tests/test_db.py`:

```python
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.core.config import Settings
from app.persistence.db import SCHEMA_VERSION, Database


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def test_connect_enables_wal(db: Database) -> None:
    row = await db.fetchone("PRAGMA journal_mode")
    assert row is not None
    assert row[0] == "wal"


async def test_connect_creates_schema(db: Database) -> None:
    rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    names = {row[0] for row in rows}
    assert {"monitor_state", "checks", "outbox", "deliveries"} <= names


async def test_connect_sets_schema_version(db: Database) -> None:
    row = await db.fetchone("PRAGMA user_version")
    assert row is not None
    assert row[0] == SCHEMA_VERSION


async def test_connect_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "twice.db"
    first = Database(path)
    await first.connect()
    await first.close()
    second = Database(path)
    await second.connect()
    row = await second.fetchone("SELECT count(*) FROM sqlite_master WHERE type='table'")
    await second.close()
    assert row is not None
    assert row[0] >= 4


def test_settings_persistence_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.db_path == Path("data/web_monitor.db")
    assert settings.retention_days == 30
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.persistence'` (and the settings test fails on missing attributes).

- [ ] **Step 6: Implement `app/persistence/db.py`**

```python
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1


class Database:
    """SQLite (WAL) connection wrapper with schema init (PLAN.md §5.1, D6)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        conn.isolation_level = None
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.executescript(SCHEMA_PATH.read_text())
        await conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._conn = conn

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = self.connection
        await conn.execute("BEGIN")
        try:
            yield conn
        except BaseException:
            await conn.rollback()
            raise
        else:
            await conn.commit()

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self.connection.execute(sql, params)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self.connection.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self.connection.execute(sql, params)
        return list(await cursor.fetchall())
```

- [ ] **Step 7: Extend `Settings`**

In `app/core/config.py`, add imports and fields so the file becomes:

```python
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WM_", extra="ignore")

    app_name: str = "web-monitor"
    log_level: LogLevel = "INFO"
    timezone: str = "UTC"
    db_path: Path = Path("data/web_monitor.db")
    retention_days: int = Field(default=30, ge=1)


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (5 passed).

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock app/persistence/__init__.py app/persistence/schema.sql app/persistence/db.py app/core/config.py tests/test_db.py
git commit -m "feat: add sqlite database with wal and schema"
```

---

### Task 2: Repositories

**Files:**
- Create: `app/persistence/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `Database` (Task 1), `Clock`, domain models.
- Produces:
  - `DeliveryStatus(StrEnum)`: PENDING/DELIVERED/FAILED.
  - `PendingDelivery` frozen dataclass: `delivery_id: int, outbox_id: int, channel: str, attempts: int, event: DomainEvent`.
  - `MonitorStateRepository(db, clock=None)`: `await save(state)`, `await load(monitor_id) -> MonitorState | None`, `await load_all() -> dict[str, MonitorState]`.
  - `CheckRepository(db, clock=None)`: `await add(monitor_id, result)`, `await recent(monitor_id, limit=50) -> list[CheckResult]`, `await prune(before: datetime) -> int`.
  - `OutboxRepository(db, clock=None)`: `await add_event(event) -> int`, `await add_deliveries(outbox_id, channels)`, `await pending_deliveries(now, limit=100) -> list[PendingDelivery]`, `await mark_delivered(delivery_id, attempts, now)`, `await mark_pending(delivery_id, attempts, next_attempt_at, error, now)`, `await mark_failed(delivery_id, attempts, error, now)`.

- [ ] **Step 1: Write the failing test**

`tests/test_repository.py`:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.models import CheckResult, DomainEvent, EventType, MonitorState, Status
from app.persistence.db import Database
from app.persistence.repository import (
    CheckRepository,
    DeliveryStatus,
    MonitorStateRepository,
    OutboxRepository,
)
from tests.conftest import FakeClock


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "repo.db")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_result(clock: FakeClock, ok: bool = True) -> CheckResult:
    return CheckResult(ok=ok, status_code=200 if ok else 503, latency_ms=12.5, checked_at=clock.now())


def make_event(clock: FakeClock) -> DomainEvent:
    return DomainEvent(
        type=EventType.SERVICE_DOWN,
        monitor_id="blog",
        payload={"name": "Blog"},
        occurred_at=clock.now(),
    )


async def test_state_roundtrip(db: Database, clock: FakeClock) -> None:
    repo = MonitorStateRepository(db, clock=clock)
    state = MonitorState(
        monitor_id="blog",
        current_status=Status.DOWN,
        consecutive_failures=3,
        last_check_at=clock.now(),
        last_result=make_result(clock, ok=False),
        down_since=clock.now(),
        last_alert_at=clock.now(),
    )
    await repo.save(state)
    loaded = await repo.load("blog")
    assert loaded is not None
    assert loaded.current_status is Status.DOWN
    assert loaded.consecutive_failures == 3
    assert loaded.last_result is not None
    assert loaded.last_result.ok is False
    assert loaded.down_since == clock.now()


async def test_state_save_is_upsert(db: Database, clock: FakeClock) -> None:
    repo = MonitorStateRepository(db, clock=clock)
    await repo.save(MonitorState(monitor_id="blog", consecutive_failures=1))
    await repo.save(MonitorState(monitor_id="blog", consecutive_failures=2))
    loaded = await repo.load("blog")
    assert loaded is not None
    assert loaded.consecutive_failures == 2


async def test_state_load_missing_returns_none(db: Database, clock: FakeClock) -> None:
    repo = MonitorStateRepository(db, clock=clock)
    assert await repo.load("nope") is None
    assert await repo.load_all() == {}


async def test_checks_add_recent_and_prune(db: Database, clock: FakeClock) -> None:
    repo = CheckRepository(db, clock=clock)
    await repo.add("blog", make_result(clock))
    clock.advance(3600)
    await repo.add("blog", make_result(clock, ok=False))
    recent = await repo.recent("blog")
    assert [result.ok for result in recent] == [True, False]
    clock.advance(86400 * 40)
    removed = await repo.prune(clock.now() - timedelta(days=30))
    assert removed == 2
    assert await repo.recent("blog") == []


async def test_outbox_event_is_idempotent(db: Database, clock: FakeClock) -> None:
    repo = OutboxRepository(db, clock=clock)
    event = make_event(clock)
    first = await repo.add_event(event)
    second = await repo.add_event(event)
    assert first == second
    rows = await db.fetchall("SELECT count(*) FROM outbox")
    assert rows[0][0] == 1


async def test_deliveries_and_pending_filtering(db: Database, clock: FakeClock) -> None:
    repo = OutboxRepository(db, clock=clock)
    outbox_id = await repo.add_event(make_event(clock))
    await repo.add_deliveries(outbox_id, ["telegram-home", "email-work"])
    pending = await repo.pending_deliveries(clock.now())
    assert {item.channel for item in pending} == {"telegram-home", "email-work"}
    assert all(item.attempts == 0 for item in pending)
    assert pending[0].event.type is EventType.SERVICE_DOWN


async def test_mark_transitions(db: Database, clock: FakeClock) -> None:
    repo = OutboxRepository(db, clock=clock)
    outbox_id = await repo.add_event(make_event(clock))
    await repo.add_deliveries(outbox_id, ["telegram-home"])
    item = (await repo.pending_deliveries(clock.now()))[0]
    await repo.mark_pending(item.delivery_id, 1, clock.now() + timedelta(seconds=30), "boom", clock.now())
    assert await repo.pending_deliveries(clock.now()) == []
    clock.advance(31)
    assert len(await repo.pending_deliveries(clock.now())) == 1
    await repo.mark_delivered(item.delivery_id, 2, clock.now())
    assert await repo.pending_deliveries(clock.now()) == []
    rows = await db.fetchall("SELECT status FROM deliveries WHERE id=?", (item.delivery_id,))
    assert rows[0][0] == DeliveryStatus.DELIVERED.value


async def test_mark_failed(db: Database, clock: FakeClock) -> None:
    repo = OutboxRepository(db, clock=clock)
    outbox_id = await repo.add_event(make_event(clock))
    await repo.add_deliveries(outbox_id, ["telegram-home"])
    item = (await repo.pending_deliveries(clock.now()))[0]
    await repo.mark_failed(item.delivery_id, 5, "gave up", clock.now())
    assert await repo.pending_deliveries(clock.now()) == []
    rows = await db.fetchall("SELECT status, last_error FROM deliveries WHERE id=?", (item.delivery_id,))
    assert rows[0][0] == DeliveryStatus.FAILED.value
    assert rows[0][1] == "gave up"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.persistence.repository'`.

- [ ] **Step 3: Implement `app/persistence/repository.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domain.clock import Clock, SystemClock
from app.domain.models import (
    CheckResult,
    DomainEvent,
    EventType,
    MonitorState,
    Status,
)
from app.persistence.db import Database


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass(frozen=True)
class PendingDelivery:
    delivery_id: int
    outbox_id: int
    channel: str
    attempts: int
    event: DomainEvent


def _dt_to_db(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _dt_from_db(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _row_to_event(row: Any) -> DomainEvent:
    return DomainEvent(
        id=UUID(str(row["event_id"])),
        type=EventType(str(row["event_type"])),
        monitor_id=str(row["monitor_id"]),
        payload=json.loads(str(row["payload"])),
        occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
    )


class MonitorStateRepository:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def save(self, state: MonitorState) -> None:
        await self._db.execute(
            """
            INSERT INTO monitor_state (
                monitor_id, current_status, consecutive_failures, last_check_at,
                last_result, down_since, last_alert_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(monitor_id) DO UPDATE SET
                current_status=excluded.current_status,
                consecutive_failures=excluded.consecutive_failures,
                last_check_at=excluded.last_check_at,
                last_result=excluded.last_result,
                down_since=excluded.down_since,
                last_alert_at=excluded.last_alert_at,
                updated_at=excluded.updated_at
            """,
            (
                state.monitor_id,
                state.current_status.value,
                state.consecutive_failures,
                _dt_to_db(state.last_check_at),
                state.last_result.model_dump_json() if state.last_result else None,
                _dt_to_db(state.down_since),
                _dt_to_db(state.last_alert_at),
                _dt_to_db(self._clock.now()),
            ),
        )

    async def load(self, monitor_id: str) -> MonitorState | None:
        row = await self._db.fetchone("SELECT * FROM monitor_state WHERE monitor_id=?", (monitor_id,))
        if row is None:
            return None
        raw_result = row["last_result"]
        return MonitorState(
            monitor_id=str(row["monitor_id"]),
            current_status=Status(str(row["current_status"])),
            consecutive_failures=int(row["consecutive_failures"]),
            last_check_at=_dt_from_db(row["last_check_at"]),
            last_result=CheckResult.model_validate_json(str(raw_result)) if raw_result else None,
            down_since=_dt_from_db(row["down_since"]),
            last_alert_at=_dt_from_db(row["last_alert_at"]),
        )

    async def load_all(self) -> dict[str, MonitorState]:
        rows = await self._db.fetchall("SELECT monitor_id FROM monitor_state")
        states: dict[str, MonitorState] = {}
        for row in rows:
            monitor_id = str(row["monitor_id"])
            loaded = await self.load(monitor_id)
            if loaded is not None:
                states[monitor_id] = loaded
        return states


class CheckRepository:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def add(self, monitor_id: str, result: CheckResult) -> None:
        await self._db.execute(
            """
            INSERT INTO checks (monitor_id, checked_at, ok, status_code, latency_ms, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id,
                _dt_to_db(result.checked_at),
                1 if result.ok else 0,
                result.status_code,
                result.latency_ms,
                result.error,
            ),
        )

    async def recent(self, monitor_id: str, limit: int = 50) -> list[CheckResult]:
        rows = await self._db.fetchall(
            """
            SELECT checked_at, ok, status_code, latency_ms, error
            FROM checks WHERE monitor_id=? ORDER BY id DESC LIMIT ?
            """,
            (monitor_id, limit),
        )
        return [
            CheckResult(
                ok=bool(row["ok"]),
                status_code=row["status_code"],
                latency_ms=row["latency_ms"],
                error=row["error"],
                checked_at=datetime.fromisoformat(str(row["checked_at"])),
            )
            for row in rows
        ]

    async def prune(self, before: datetime) -> int:
        cursor = await self._db.connection.execute(
            "DELETE FROM checks WHERE checked_at < ?", (_dt_to_db(before),)
        )
        return cursor.rowcount if cursor.rowcount is not None else 0


class OutboxRepository:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def add_event(self, event: DomainEvent) -> int:
        await self._db.execute(
            """
            INSERT OR IGNORE INTO outbox
                (event_id, event_type, monitor_id, payload, occurred_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.id),
                event.type.value,
                event.monitor_id,
                json.dumps(event.payload),
                _dt_to_db(event.occurred_at),
                _dt_to_db(self._clock.now()),
            ),
        )
        row = await self._db.fetchone("SELECT id FROM outbox WHERE event_id=?", (str(event.id),))
        if row is None:
            raise RuntimeError(f"outbox event {event.id} not persisted")
        return int(row["id"])

    async def add_deliveries(self, outbox_id: int, channels: list[str]) -> None:
        now = _dt_to_db(self._clock.now())
        for channel in channels:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO deliveries
                    (outbox_id, channel, status, attempts, next_attempt_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (outbox_id, channel, DeliveryStatus.PENDING.value, now, now),
            )

    async def pending_deliveries(self, now: datetime, limit: int = 100) -> list[PendingDelivery]:
        rows = await self._db.fetchall(
            """
            SELECT d.id AS delivery_id, d.outbox_id, d.channel, d.attempts,
                   o.event_id, o.event_type, o.monitor_id, o.payload, o.occurred_at
            FROM deliveries d JOIN outbox o ON o.id = d.outbox_id
            WHERE d.status = ? AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
            ORDER BY d.id LIMIT ?
            """,
            (DeliveryStatus.PENDING.value, _dt_to_db(now), limit),
        )
        return [
            PendingDelivery(
                delivery_id=int(row["delivery_id"]),
                outbox_id=int(row["outbox_id"]),
                channel=str(row["channel"]),
                attempts=int(row["attempts"]),
                event=_row_to_event(row),
            )
            for row in rows
        ]

    async def mark_delivered(self, delivery_id: int, attempts: int, now: datetime) -> None:
        await self._db.execute(
            "UPDATE deliveries SET status=?, attempts=?, last_error=NULL, updated_at=? WHERE id=?",
            (DeliveryStatus.DELIVERED.value, attempts, _dt_to_db(now), delivery_id),
        )

    async def mark_pending(
        self,
        delivery_id: int,
        attempts: int,
        next_attempt_at: datetime,
        error: str,
        now: datetime,
    ) -> None:
        await self._db.execute(
            """
            UPDATE deliveries
            SET status=?, attempts=?, next_attempt_at=?, last_error=?, updated_at=?
            WHERE id=?
            """,
            (
                DeliveryStatus.PENDING.value,
                attempts,
                _dt_to_db(next_attempt_at),
                error,
                _dt_to_db(now),
                delivery_id,
            ),
        )

    async def mark_failed(self, delivery_id: int, attempts: int, error: str, now: datetime) -> None:
        await self._db.execute(
            """
            UPDATE deliveries
            SET status=?, attempts=?, last_error=?, updated_at=?
            WHERE id=?
            """,
            (DeliveryStatus.FAILED.value, attempts, error, _dt_to_db(now), delivery_id),
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_repository.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add app/persistence/repository.py tests/test_repository.py
git commit -m "feat: add repositories for state, checks, and outbox"
```

---

### Task 3: Store facade — write path, routing, rehydration, pruning

**Files:**
- Create: `app/persistence/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Database`, repositories (Task 2), `Clock`, `Monitor`, `MonitorState`, `CheckResult`, `DomainEvent`, `EventType`, `ChannelConfig`.
- Produces:
  - `channels_for_event(event_type, monitor, channels) -> list[str]` (pure).
  - `Store(db, clock=None)` with attributes `db`, `states`, `checks`, `outbox`.
  - `await Store.record_check(monitor, state, result, events, channels) -> None` (single transaction).
  - `await Store.rehydrate(monitors) -> dict[str, MonitorState]`.
  - `await Store.prune_checks(retention_days) -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:

```python
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from app.domain.models import (
    ChannelConfig,
    ChannelType,
    CheckResult,
    DomainEvent,
    EventType,
    HttpCheckConfig,
    Monitor,
    MonitorState,
    Status,
)
from app.persistence.db import Database
from app.persistence.store import Store, channels_for_event
from tests.conftest import FakeClock


@pytest.fixture
async def store(tmp_path: Path, fake_clock: FakeClock) -> AsyncIterator[Store]:
    db = Database(tmp_path / "store.db")
    await db.connect()
    yield Store(db, clock=fake_clock)
    await db.close()


def make_channels() -> dict[str, ChannelConfig]:
    return {
        "telegram-home": ChannelConfig(name="telegram-home", type=ChannelType.TELEGRAM),
        "email-work": ChannelConfig(
            name="email-work",
            type=ChannelType.TELEGRAM,
            events=[EventType.SERVICE_RECOVERED],
        ),
    }


def make_monitor(channels: list[str] | None = None) -> Monitor:
    return Monitor(
        id="blog",
        name="Blog",
        check_config=HttpCheckConfig(url="https://example.com"),
        channels=channels,
    )


def test_channels_for_event_respects_override_and_subscription() -> None:
    channels = make_channels()
    monitor = make_monitor(channels=["email-work"])
    assert channels_for_event(EventType.SERVICE_DOWN, monitor, channels) == []
    assert channels_for_event(EventType.SERVICE_RECOVERED, monitor, channels) == ["email-work"]


def test_channels_for_event_defaults_to_all() -> None:
    channels = make_channels()
    monitor = make_monitor()
    assert channels_for_event(EventType.SERVICE_DOWN, monitor, channels) == ["telegram-home"]


async def test_record_check_persists_state_check_and_outbox(store: Store, fake_clock: FakeClock) -> None:
    monitor = make_monitor()
    result = CheckResult(ok=False, status_code=503, error="boom", checked_at=fake_clock.now())
    state = MonitorState(
        monitor_id="blog",
        current_status=Status.DOWN,
        consecutive_failures=3,
        last_check_at=fake_clock.now(),
        last_result=result,
        down_since=fake_clock.now(),
    )
    event = DomainEvent(
        type=EventType.SERVICE_DOWN,
        monitor_id="blog",
        payload={"name": "Blog"},
        occurred_at=fake_clock.now(),
    )
    await store.record_check(monitor, state, result, [event], make_channels())

    assert await store.states.load("blog") is not None
    assert len(await store.checks.recent("blog")) == 1
    pending = await store.outbox.pending_deliveries(fake_clock.now())
    assert [item.channel for item in pending] == ["telegram-home"]


async def test_record_check_rolls_back_on_failure(store: Store, fake_clock: FakeClock) -> None:
    monitor = make_monitor()
    result = CheckResult(ok=False, error="boom", checked_at=fake_clock.now())
    state = MonitorState(monitor_id="blog", last_result=result, last_check_at=fake_clock.now())
    event = DomainEvent(
        type=EventType.SERVICE_DOWN,
        monitor_id="blog",
        payload={"name": "Blog"},
        occurred_at=fake_clock.now(),
    )
    with pytest.raises(RuntimeError):
        await store.record_check(monitor, state, result, [event, event], make_channels())
    assert await store.states.load("blog") is None


async def test_rehydrate_roundtrip_and_default(store: Store, fake_clock: FakeClock) -> None:
    await store.states.save(
        MonitorState(monitor_id="blog", current_status=Status.DOWN, consecutive_failures=3)
    )
    states = await store.rehydrate([make_monitor(), Monitor(id="api", name="API", check_config=HttpCheckConfig(url="https://api.example.com"))])
    assert states["blog"].current_status is Status.DOWN
    assert states["blog"].consecutive_failures == 3
    assert states["api"].current_status is Status.UNKNOWN


async def test_prune_checks_removes_old_rows(store: Store, fake_clock: FakeClock) -> None:
    result = CheckResult(ok=True, checked_at=fake_clock.now())
    await store.checks.add("blog", result)
    fake_clock.advance(timedelta(days=40).total_seconds())
    removed = await store.prune_checks(30)
    assert removed == 1
    assert await store.checks.recent("blog") == []
```

Note: `test_record_check_rolls_back_on_failure` intentionally triggers the `add_event` failure path by reusing one event whose `event_id` is UNIQUE; `add_event` uses `INSERT OR IGNORE` then re-selects, so it does NOT fail. Replace the failure trigger in Step 3 by passing a monkeypatched repository method — see the implementation note in Step 3 for the exact test body to use.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.persistence.store'`.

- [ ] **Step 3: Implement `app/persistence/store.py`**

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta

from app.domain.clock import Clock, SystemClock
from app.domain.models import (
    ChannelConfig,
    CheckResult,
    DomainEvent,
    EventType,
    Monitor,
    MonitorState,
)
from app.persistence.db import Database
from app.persistence.repository import CheckRepository, MonitorStateRepository, OutboxRepository


def channels_for_event(
    event_type: EventType,
    monitor: Monitor,
    channels: Mapping[str, ChannelConfig],
) -> list[str]:
    names = monitor.channels if monitor.channels is not None else list(channels)
    return [
        name
        for name in names
        if name in channels and event_type in channels[name].events
    ]


class Store:
    """Repository facade + high-level persistence operations (PLAN.md §5.1, §6.2)."""

    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self.db = db
        self._clock = clock or SystemClock()
        self.states = MonitorStateRepository(db, clock=self._clock)
        self.checks = CheckRepository(db, clock=self._clock)
        self.outbox = OutboxRepository(db, clock=self._clock)

    async def record_check(
        self,
        monitor: Monitor,
        state: MonitorState,
        result: CheckResult,
        events: Sequence[DomainEvent],
        channels: Mapping[str, ChannelConfig],
    ) -> None:
        async with self.db.transaction():
            await self.states.save(state)
            await self.checks.add(monitor.id, result)
            for event in events:
                outbox_id = await self.outbox.add_event(event)
                names = channels_for_event(event.type, monitor, channels)
                if names:
                    await self.outbox.add_deliveries(outbox_id, names)

    async def rehydrate(self, monitors: Sequence[Monitor]) -> dict[str, MonitorState]:
        stored = await self.states.load_all()
        return {
            monitor.id: stored.get(monitor.id, MonitorState(monitor_id=monitor.id))
            for monitor in monitors
        }

    async def prune_checks(self, retention_days: int) -> int:
        cutoff = self._clock.now() - timedelta(days=retention_days)
        return await self.checks.prune(cutoff)
```

For the rollback test, replace `test_record_check_rolls_back_on_failure` in `tests/test_store.py` with this body (monkeypatch forces `states.save` to raise mid-transaction):

```python
async def test_record_check_rolls_back_on_failure(
    store: Store, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monitor = make_monitor()
    result = CheckResult(ok=False, error="boom", checked_at=fake_clock.now())
    state = MonitorState(monitor_id="blog", last_result=result, last_check_at=fake_clock.now())
    event = DomainEvent(
        type=EventType.SERVICE_DOWN,
        monitor_id="blog",
        payload={"name": "Blog"},
        occurred_at=fake_clock.now(),
    )

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("write failed")

    monkeypatch.setattr(store.states, "save", boom)
    with pytest.raises(RuntimeError):
        await store.record_check(monitor, state, result, [event], make_channels())
    assert await store.checks.recent("blog") == []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add app/persistence/store.py tests/test_store.py
git commit -m "feat: add persistence store with routing, rehydration, and pruning"
```

---

### Task 4: Crash-resume dispatcher

**Files:**
- Create: `app/dispatch/__init__.py`, `app/dispatch/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `Store`, `PendingDelivery`, `Clock`, `DomainEvent`, `ChannelConfig`.
- Produces:
  - `DeliveryHandler = Callable[[DomainEvent, ChannelConfig], Awaitable[None]]`.
  - `Dispatcher(store, channels, handler, *, clock=None, max_attempts=5, backoff_base_s=30.0, backoff_max_s=3600.0, batch_size=100)`.
  - `await Dispatcher.run_once() -> int` (number of deliveries processed).

- [ ] **Step 1: Write the failing test**

`tests/test_dispatcher.py`:

```python
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from app.dispatch.dispatcher import Dispatcher
from app.domain.models import (
    ChannelConfig,
    ChannelType,
    DomainEvent,
    EventType,
)
from app.persistence.db import Database
from app.persistence.store import Store
from tests.conftest import FakeClock


@pytest.fixture
async def store(tmp_path: Path, fake_clock: FakeClock) -> AsyncIterator[Store]:
    db = Database(tmp_path / "dispatch.db")
    await db.connect()
    yield Store(db, clock=fake_clock)
    await db.close()


def make_channels() -> dict[str, ChannelConfig]:
    return {
        "telegram-home": ChannelConfig(name="telegram-home", type=ChannelType.TELEGRAM),
        "email-work": ChannelConfig(name="email-work", type=ChannelType.TELEGRAM),
    }


async def enqueue(store: Store, clock: FakeClock, channel: str) -> None:
    event = DomainEvent(
        type=EventType.SERVICE_DOWN,
        monitor_id="blog",
        payload={"name": "Blog"},
        occurred_at=clock.now(),
    )
    outbox_id = await store.outbox.add_event(event)
    await store.outbox.add_deliveries(outbox_id, [channel])


async def test_successful_delivery_marks_delivered(store: Store, fake_clock: FakeClock) -> None:
    await enqueue(store, fake_clock, "telegram-home")
    sent: list[str] = []

    async def handler(event: DomainEvent, channel: ChannelConfig) -> None:
        sent.append(channel.name)

    dispatcher = Dispatcher(store, make_channels(), handler, clock=fake_clock)
    processed = await dispatcher.run_once()
    assert processed == 1
    assert sent == ["telegram-home"]
    assert await store.outbox.pending_deliveries(fake_clock.now()) == []


async def test_failure_schedules_backoff_retry(store: Store, fake_clock: FakeClock) -> None:
    await enqueue(store, fake_clock, "telegram-home")

    async def handler(event: DomainEvent, channel: ChannelConfig) -> None:
        raise RuntimeError("network down")

    dispatcher = Dispatcher(
        store, make_channels(), handler, clock=fake_clock, backoff_base_s=10.0
    )
    await dispatcher.run_once()
    assert await store.outbox.pending_deliveries(fake_clock.now()) == []
    fake_clock.advance(9)
    assert await store.outbox.pending_deliveries(fake_clock.now()) == []
    fake_clock.advance(2)
    pending = await store.outbox.pending_deliveries(fake_clock.now())
    assert len(pending) == 1
    assert pending[0].attempts == 1


async def test_max_attempts_marks_failed(store: Store, fake_clock: FakeClock) -> None:
    await enqueue(store, fake_clock, "telegram-home")

    async def handler(event: DomainEvent, channel: ChannelConfig) -> None:
        raise RuntimeError("always down")

    dispatcher = Dispatcher(
        store,
        make_channels(),
        handler,
        clock=fake_clock,
        max_attempts=3,
        backoff_base_s=1.0,
    )
    for _ in range(3):
        await dispatcher.run_once()
        fake_clock.advance(10)
    assert await store.outbox.pending_deliveries(fake_clock.now()) == []
    rows = await store.db.fetchall("SELECT status, attempts FROM deliveries")
    assert rows[0][0] == "failed"
    assert rows[0][1] == 3


async def test_crash_resume_with_new_dispatcher(store: Store, fake_clock: FakeClock) -> None:
    await enqueue(store, fake_clock, "telegram-home")

    async def failing(event: DomainEvent, channel: ChannelConfig) -> None:
        raise RuntimeError("network down")

    first = Dispatcher(store, make_channels(), failing, clock=fake_clock, backoff_base_s=10.0)
    await first.run_once()
    fake_clock.advance(11)

    delivered: list[str] = []

    async def succeeding(event: DomainEvent, channel: ChannelConfig) -> None:
        delivered.append(channel.name)

    second = Dispatcher(store, make_channels(), succeeding, clock=fake_clock)
    processed = await second.run_once()
    assert processed == 1
    assert delivered == ["telegram-home"]


async def test_unknown_channel_is_failed(store: Store, fake_clock: FakeClock) -> None:
    await enqueue(store, fake_clock, "ghost")

    async def handler(event: DomainEvent, channel: ChannelConfig) -> None:
        raise AssertionError("should not be called")

    dispatcher = Dispatcher(store, make_channels(), handler, clock=fake_clock)
    await dispatcher.run_once()
    rows = await store.db.fetchall("SELECT status, last_error FROM deliveries")
    assert rows[0][0] == "failed"
    assert "unknown channel" in rows[0][1]


async def test_per_channel_isolation(store: Store, fake_clock: FakeClock) -> None:
    await enqueue(store, fake_clock, "telegram-home")
    await enqueue(store, fake_clock, "email-work")

    async def handler(event: DomainEvent, channel: ChannelConfig) -> None:
        if channel.name == "telegram-home":
            raise RuntimeError("telegram down")

    dispatcher = Dispatcher(
        store, make_channels(), handler, clock=fake_clock, backoff_base_s=10.0
    )
    await dispatcher.run_once()
    rows = await store.db.fetchall("SELECT channel, status FROM deliveries ORDER BY channel")
    assert rows[0] == ("email-work", "delivered")
    assert rows[1] == ("telegram-home", "pending")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.dispatch'`.

- [ ] **Step 3: Write `app/dispatch/__init__.py`** (empty file)

- [ ] **Step 4: Implement `app/dispatch/dispatcher.py`**

```python
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

from app.domain.clock import Clock, SystemClock
from app.domain.models import ChannelConfig, DomainEvent
from app.persistence.store import Store

logger = logging.getLogger(__name__)

DeliveryHandler = Callable[[DomainEvent, ChannelConfig], Awaitable[None]]


class Dispatcher:
    """Delivers outbox events to channels, with DB-backed retry/backoff (PLAN.md §6.4)."""

    def __init__(
        self,
        store: Store,
        channels: Mapping[str, ChannelConfig],
        handler: DeliveryHandler,
        *,
        clock: Clock | None = None,
        max_attempts: int = 5,
        backoff_base_s: float = 30.0,
        backoff_max_s: float = 3600.0,
        batch_size: int = 100,
    ) -> None:
        self._store = store
        self._channels = channels
        self._handler = handler
        self._clock = clock or SystemClock()
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._batch_size = batch_size

    def _backoff_seconds(self, attempts: int) -> float:
        return min(self._backoff_base_s * (2 ** (attempts - 1)), self._backoff_max_s)

    async def run_once(self) -> int:
        now = self._clock.now()
        pending = await self._store.outbox.pending_deliveries(now, self._batch_size)
        processed = 0
        for item in pending:
            channel = self._channels.get(item.channel)
            if channel is None:
                await self._store.outbox.mark_failed(
                    item.delivery_id, item.attempts, f"unknown channel {item.channel!r}", self._clock.now()
                )
                processed += 1
                continue
            attempts = item.attempts + 1
            try:
                await self._handler(item.event, channel)
            except Exception as exc:
                logger.warning(
                    "delivery %s to %s failed (attempt %s/%s): %s",
                    item.delivery_id,
                    item.channel,
                    attempts,
                    self._max_attempts,
                    exc,
                )
                if attempts >= self._max_attempts:
                    await self._store.outbox.mark_failed(
                        item.delivery_id, attempts, str(exc), self._clock.now()
                    )
                else:
                    next_attempt_at = self._clock.now() + timedelta(
                        seconds=self._backoff_seconds(attempts)
                    )
                    await self._store.outbox.mark_pending(
                        item.delivery_id, attempts, next_attempt_at, str(exc), self._clock.now()
                    )
            else:
                await self._store.outbox.mark_delivered(
                    item.delivery_id, attempts, self._clock.now()
                )
            processed += 1
        return processed
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_dispatcher.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add app/dispatch/__init__.py app/dispatch/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: add crash-resume notification dispatcher with retry backoff"
```

---

### Task 5: Acceptance

**Files:**
- Modify: `PLAN.md`

**Interfaces:**
- Consumes: everything above.
- Produces: a green milestone and its doc marker. No new code.

- [ ] **Step 1: Run the full gate**

Run: `uv sync --frozen && make gate`
Expected: ruff check passes; `ruff format --check .` passes (if it flags `PLAN.md` or this plan doc, run `uv run ruff format PLAN.md docs/superpowers/plans/2026-09-07-phase-d-persistence-outbox.md` and re-run); mypy Success; pytest green — expect ~82 passed (60 prior + 5 db + 8 repository + 6 store + 6 dispatcher; report the exact count).

- [ ] **Step 2: Mark the milestone in `PLAN.md`**

Insert this line directly ABOVE the existing line `**Phase D — Persistence + outbox**` in §15:

```
> ✅ Done (see `docs/superpowers/plans/2026-09-07-phase-d-persistence-outbox.md`).
```

- [ ] **Step 3: Commit**

```bash
git add PLAN.md docs/superpowers/plans/2026-09-07-phase-d-persistence-outbox.md
git commit -m "docs: mark phase d persistence + outbox complete"
```

- [ ] **Step 4: STOP**

Do NOT push, merge, or touch `main`. Report to the parent session: per-task commit hashes, final gate output, exact pytest count, and any deviations from this plan (with file:line specifics). Confirm the working tree is clean and you are still on the feature branch.
