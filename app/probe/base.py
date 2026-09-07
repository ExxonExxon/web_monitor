from typing import Protocol

from app.domain.models import CheckResult, HttpCheckConfig


class CheckStrategy(Protocol):
    """One strategy knows how to probe one kind of target (PLAN.md §7)."""

    async def run(self, cfg: HttpCheckConfig, timeout_s: float) -> CheckResult: ...
