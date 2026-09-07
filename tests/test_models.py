import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.models import (
    ChannelConfig,
    CheckResult,
    DomainEvent,
    EventType,
    HttpCheckConfig,
    Monitor,
    MonitorFile,
    MonitorState,
    Status,
)


def result_ts() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


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
        Monitor.model_validate({"id": "blog", "name": "Blog", "check_config": {"url": "not-a-url"}})


def test_monitor_state_defaults() -> None:
    state = MonitorState(monitor_id="blog")
    assert state.current_status == Status.UNKNOWN
    assert state.consecutive_failures == 0
    assert state.last_check_at is None
    assert state.last_result is None
    assert state.down_since is None
    assert state.last_alert_at is None


def test_check_result_defaults() -> None:
    result = CheckResult(ok=False, checked_at=result_ts())
    assert result.status_code is None
    assert result.latency_ms is None
    assert result.error is None


def test_domain_event_has_uuid_and_timestamp() -> None:
    event = DomainEvent(type=EventType.SERVICE_DOWN, monitor_id="blog", occurred_at=result_ts())
    assert isinstance(event.id, uuid.UUID)
    assert event.payload == {}
    assert event.occurred_at.year == 2026


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
