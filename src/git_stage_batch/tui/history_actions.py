"""Undo and redo actions for interactive mode."""

from __future__ import annotations

from ..commands.redo import command_redo
from ..commands.undo import command_undo
from .flow import FlowState


def handle_redo(flow_state: FlowState) -> None:
    """Handle redo action."""
    command_redo()


def handle_undo(flow_state: FlowState) -> None:
    """Handle undo action."""
    command_undo()
