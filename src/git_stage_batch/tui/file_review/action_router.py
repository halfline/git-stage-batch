"""Line, file, and replacement action routing for file review."""

from __future__ import annotations

import sys

from ...exceptions import CommandError
from ...i18n import _
from .batch_actions import (
    apply_batch_file_action,
    apply_batch_line_action,
    apply_batch_replacement_action,
)
from .live_actions import (
    apply_live_file_action,
    apply_live_line_action,
    apply_live_replacement_action,
)
from .session import FileReviewSessionState
from ..flow import LocationRole
from ..prompts import (
    confirm_destructive_operation,
    prompt_line_ids,
    wrap_prompt_for_readline,
)


def apply_replacement_action(state: FileReviewSessionState) -> None:
    """Prompt for replacement text and route the replacement action."""
    line_ids = prompt_line_ids()
    if not line_ids:
        return

    replacement_text = _prompt_replacement_text()
    if replacement_text is None:
        return

    try:
        if state.flow_state.source.role is LocationRole.BATCH:
            apply_batch_replacement_action(state, line_ids, replacement_text)
        else:
            apply_live_replacement_action(state, line_ids, replacement_text)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def apply_line_action(state: FileReviewSessionState, action: str) -> None:
    """Prompt for line IDs and route the line action."""
    if action == "s" and state.flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return

    line_ids = prompt_line_ids()
    if not line_ids:
        return

    if action == "d" and state.flow_state.source.role is LocationRole.WORKING_TREE:
        if not confirm_destructive_operation(
            "discard",
            _("This will discard the selected lines from your working tree."),
        ):
            return

    try:
        if state.flow_state.source.role is LocationRole.BATCH:
            apply_batch_line_action(state, action, line_ids)
        else:
            apply_live_line_action(state, action, line_ids)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def apply_file_action(state: FileReviewSessionState, action: str) -> None:
    """Route a whole-file action for the reviewed file."""
    if action == "S" and state.flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return

    if action == "D" and state.flow_state.source.role is LocationRole.WORKING_TREE:
        if not confirm_destructive_operation(
            "discard",
            _("This will discard the reviewed file from your working tree."),
        ):
            return

    try:
        if state.flow_state.source.role is LocationRole.BATCH:
            apply_batch_file_action(state, action)
        else:
            apply_live_file_action(state, action)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def _prompt_replacement_text() -> str | None:
    try:
        value = input(
            wrap_prompt_for_readline(_("Replacement text (empty cancels): "))
        )
    except (KeyboardInterrupt, EOFError):
        return None
    if value == "":
        return None
    return value
