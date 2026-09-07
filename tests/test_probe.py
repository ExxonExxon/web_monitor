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
    cfg = make_cfg(method="POST", expected_status=204)
    with respx.mock:
        respx.post("https://example.com/").mock(return_value=httpx.Response(204))
        result = await strategy.run(cfg, timeout_s=5.0)
        request = respx.calls.last.request
    assert result.ok is True
    assert request.method == "POST"


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
