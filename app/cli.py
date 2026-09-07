import asyncio
from pathlib import Path
from typing import Annotated

import typer

from app.config.loader import ConfigError, load_config
from app.domain.models import CheckResult, Monitor, MonitorFile
from app.domain.state import MonitorStateMachine
from app.service.checks import run_check

app = typer.Typer(help="web-monitor command line interface")

DEFAULT_CONFIG_PATH = Path("config/monitors.yaml")


@app.callback()
def main() -> None:
    """web-monitor command line interface."""


@app.command("config-check")
def config_check(
    config_path: Annotated[Path, typer.Argument(help="path to monitors.yaml")] = (
        DEFAULT_CONFIG_PATH
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


@app.command("check")
def check_command(
    monitor_id: Annotated[str, typer.Argument(help="id of the monitor to probe now")],
    config: Annotated[Path, typer.Option("--config", help="path to monitors.yaml")] = (
        DEFAULT_CONFIG_PATH
    ),
) -> None:
    try:
        config_file = load_config(config)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    monitor = _find_monitor(config_file, monitor_id)
    if monitor is None:
        typer.secho(f"no monitor with id '{monitor_id}' in {config}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    machine = MonitorStateMachine(monitor)
    events = asyncio.run(run_check(monitor, machine))
    _print_check_result(machine.state.last_result)
    for event in events:
        typer.echo(f"- event {event.type.value} at {event.occurred_at.isoformat()}")


def _find_monitor(config_file: MonitorFile, monitor_id: str) -> Monitor | None:
    return next((item for item in config_file.monitors if item.id == monitor_id), None)


def _print_check_result(result: CheckResult | None) -> None:
    if result is None:
        return
    if result.ok:
        status = result.status_code if result.status_code is not None else "-"
        latency = f"{result.latency_ms:.3f}ms" if result.latency_ms is not None else "-"
        typer.secho(f"check ok: {status} in {latency}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"check failed: {result.error}", fg=typer.colors.RED)


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
