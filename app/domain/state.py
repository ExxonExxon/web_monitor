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

    @property
    def clock(self) -> Clock:
        """The single clock source injected into this state machine."""
        return self._clock

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
        return DomainEvent(
            type=event_type, monitor_id=self.monitor.id, payload=payload, occurred_at=now
        )
