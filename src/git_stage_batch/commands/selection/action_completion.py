"""Command-layer selected change flow helpers."""

from __future__ import annotations

import sys

from ...data.hunk_tracking import (
    advance_to_next_change,
    select_next_change_after_action,
)
from ...data.selected_change.store import read_selected_change_kind
from ...i18n import _
from .selected_change_display import show_selected_change


def finish_selected_change_action(
    *,
    quiet: bool,
    auto_advance: bool | None = None,
) -> None:
    """Apply the configured selection step after a hunk action completes."""
    if not select_next_change_after_action(auto_advance=auto_advance):
        return

    if quiet:
        return

    if read_selected_change_kind() is None:
        print(_("No more hunks to process."), file=sys.stderr)
        return

    show_selected_change()


def advance_to_and_show_next_change() -> None:
    """Advance to the next selected change and display it."""
    advance_to_next_change()

    if read_selected_change_kind() is None:
        print(_("No more hunks to process."), file=sys.stderr)
        return

    show_selected_change()
