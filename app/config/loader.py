from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain.models import MonitorFile


class ConfigError(Exception):
    """Raised when the monitor config file cannot be read, parsed, or validated."""


def parse_config(text: str) -> MonitorFile:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping, got {type(data).__name__}")
    try:
        return MonitorFile.model_validate(data)
    except ValidationError as exc:
        issues = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            issues.append(f"- {location}: {error['msg']}")
        raise ConfigError("invalid config:\n" + "\n".join(issues)) from exc


def load_config(path: Path) -> MonitorFile:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse_config(text)
