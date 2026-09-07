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
