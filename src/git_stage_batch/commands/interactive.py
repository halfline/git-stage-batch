"""Interactive command implementation."""

from __future__ import annotations

from ..tui.interactive import start_interactive_mode


def command_interactive() -> None:
    """Start interactive TUI mode for hunk-by-hunk staging."""
    start_interactive_mode()
