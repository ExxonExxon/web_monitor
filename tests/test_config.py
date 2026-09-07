import pytest

from app.core.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_name == "web-monitor"
    assert settings.log_level == "INFO"
    assert settings.timezone == "UTC"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WM_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("WM_LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.timezone == "Europe/Berlin"
    assert settings.log_level == "DEBUG"
