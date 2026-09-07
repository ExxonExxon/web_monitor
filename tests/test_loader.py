from pathlib import Path

import pytest

from app.config.loader import ConfigError, load_config, parse_config

VALID_YAML = """
monitors:
  - id: my-blog
    name: My Blog
    check_config:
      url: https://blog.example.com
      expected_status: 200
    retries: 5
channels:
  - name: telegram-home
    type: telegram
    events: [service_down]
"""


def test_parse_config_loads_monitors_and_channels() -> None:
    config = parse_config(VALID_YAML)
    assert len(config.monitors) == 1
    monitor = config.monitors[0]
    assert monitor.id == "my-blog"
    assert monitor.retries == 5
    assert monitor.interval_s == 60
    assert str(monitor.check_config.url) == "https://blog.example.com/"
    assert config.channels[0].events == ["service_down"]


def test_parse_config_accepts_empty_document() -> None:
    config = parse_config("")
    assert config.monitors == []
    assert config.channels == []


def test_parse_config_rejects_non_mapping_root() -> None:
    with pytest.raises(ConfigError, match="root must be a mapping"):
        parse_config("- just\n- a\n- list")


def test_parse_config_reports_yaml_syntax_error() -> None:
    with pytest.raises(ConfigError, match="invalid YAML"):
        parse_config("monitors: [unclosed")


def test_parse_config_reports_validation_issues() -> None:
    with pytest.raises(ConfigError, match="invalid config"):
        parse_config("monitors:\n  - id: one\n    check_config:\n      url: nope\n")


def test_parse_config_rejects_duplicate_ids() -> None:
    text = """
monitors:
  - id: dup
    name: One
    check_config:
      url: https://a.example.com
  - id: dup
    name: Two
    check_config:
      url: https://b.example.com
"""
    with pytest.raises(ConfigError, match="duplicate monitor ids"):
        parse_config(text)


def test_parse_config_rejects_unknown_check_type() -> None:
    text = """
monitors:
  - id: thing
    name: Thing
    check_type: tcp
    check_config:
      url: https://a.example.com
"""
    with pytest.raises(ConfigError):
        parse_config(text)


def test_load_config_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    config = load_config(path)
    assert config.monitors[0].id == "my-blog"


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")
