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
