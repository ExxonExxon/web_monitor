from pathlib import Path
from typing import Annotated

import typer

from app.config.loader import ConfigError, load_config
from app.domain.models import MonitorFile

app = typer.Typer(help="web-monitor command line interface")


@app.callback()
def main() -> None:
    """web-monitor command line interface."""


@app.command("config-check")
def config_check(
    config_path: Annotated[Path, typer.Argument(help="path to monitors.yaml")] = Path(
        "config/monitors.yaml"
    ),
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(f"config OK: {config_path}", fg=typer.colors.GREEN)
    _print_monitors(config)
    _print_channels(config)


def _print_monitors(config: MonitorFile) -> None:
    for monitor in config.monitors:
        cadence = "disabled" if not monitor.enabled else f"every {monitor.interval_s}s"
        url = str(monitor.check_config.url)
        typer.echo(
            f"- {monitor.id} ({monitor.name}): {monitor.check_type.value} {url} "
            f"[{cadence}, timeout {monitor.timeout_s}s, retries {monitor.retries}, "
            f"cooldown {monitor.cooldown_s}s]"
        )


def _print_channels(config: MonitorFile) -> None:
    for channel in config.channels:
        events = ", ".join(event.value for event in channel.events)
        typer.echo(f"- channel {channel.name}: {channel.type.value} -> [{events}]")
