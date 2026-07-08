"""Again action for interactive mode."""

from __future__ import annotations

from ..commands.again import command_again
from .flow import FlowState


def handle_again(flow_state: FlowState) -> None:
    """Handle again action."""
    command_again(quiet=True)
