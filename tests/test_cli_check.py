from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from app.cli import app
from app.domain.models import CheckResult

runner = CliRunner()

VALID_CONFIG = (
    "monitors:\n  - id: blog\n    name: Blog\n    check_config:\n      url: https://example.com\n"
)


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config" / "monitors.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(VALID_CONFIG, encoding="utf-8")
    return path


def make_result(*, ok: bool, error: str | None = None) -> CheckResult:
    return CheckResult(ok=ok, error=error, checked_at=datetime.now(UTC))


def test_check_ok(monkeypatch, tmp_path) -> None:
    async def fake_run(monitor, machine):
        machine.state.last_result = make_result(ok=True)
        return []

    monkeypatch.setattr("app.cli.run_check", fake_run)
    result = runner.invoke(app, ["check", "blog", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 0
    assert "check ok" in result.stdout


def test_check_failed_prints_failure(monkeypatch, tmp_path) -> None:
    async def fake_run(monitor, machine):
        machine.state.last_result = make_result(ok=False, error="connection refused")
        return []

    monkeypatch.setattr("app.cli.run_check", fake_run)
    result = runner.invoke(app, ["check", "blog", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 0
    assert "check failed: connection refused" in result.stdout


def test_check_unknown_monitor(tmp_path) -> None:
    result = runner.invoke(app, ["check", "nope", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 1
    assert "no monitor with id 'nope'" in result.stderr


def test_check_missing_config(tmp_path) -> None:
    result = runner.invoke(app, ["check", "blog", "--config", str(tmp_path / "none.yaml")])
    assert result.exit_code == 1
    assert "config file not found" in result.stderr
