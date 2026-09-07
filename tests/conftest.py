from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import HttpCheckConfig, Monitor


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

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
