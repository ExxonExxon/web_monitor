from app.domain.models import DomainEvent, Monitor
from app.domain.state import MonitorStateMachine
from app.probe.registry import get_strategy


async def run_check(monitor: Monitor, machine: MonitorStateMachine) -> list[DomainEvent]:
    """Probe `monitor` then feed the result through `machine` (D8 shared path)."""
    strategy = get_strategy(monitor.check_type, clock=machine.clock)
    result = await strategy.run(monitor.check_config, monitor.timeout_s)
    return machine.record(result)
