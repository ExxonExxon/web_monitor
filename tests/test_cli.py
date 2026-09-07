from pathlib import Path

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()

VALID_YAML = """
monitors:
  - id: my-blog
    name: My Blog
    check_config:
      url: https://blog.example.com
channels:
  - name: telegram-home
    type: telegram
"""


def test_config_check_reports_ok_on_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 0
    assert "config OK" in result.stdout
    assert "my-blog" in result.stdout
    assert "telegram-home" in result.stdout


def test_config_check_fails_on_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nope.yaml"
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_config_check_fails_on_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text("monitors: [unclosed", encoding="utf-8")
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 1
    assert "invalid YAML" in result.stderr


def test_config_check_fails_on_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "monitors.yaml"
    path.write_text(
        "monitors:\n  - id: one\n    check_config:\n      url: nope\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["config-check", str(path)])
    assert result.exit_code == 1
    assert "invalid config" in result.stderr


def test_config_check_default_path_is_config_monitors_yaml(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "monitors.yaml").write_text(VALID_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config-check"])
    assert result.exit_code == 0
    assert "config OK" in result.stdout
