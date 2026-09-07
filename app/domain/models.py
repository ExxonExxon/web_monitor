from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class CheckType(StrEnum):
    HTTP = "http"


class Status(StrEnum):
    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"
    PAUSED = "paused"


class EventType(StrEnum):
    SERVICE_DOWN = "service_down"
    SERVICE_RECOVERED = "service_recovered"


class ChannelType(StrEnum):
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
