"""Status action for interactive mode."""

from __future__ import annotations

from ..exceptions import BypassRefresh
from .flow import FlowState


def handle_status(flow_state: FlowState) -> None:
    """Handle status drawer."""
    from ..commands.status import command_status

    command_status()
    raise BypassRefresh()
