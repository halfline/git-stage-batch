"""Batch-source action execution for file review."""

from __future__ import annotations

import sys
from typing import Protocol

from ...i18n import _
from ..flow import FlowState, LocationRole


class FileReviewBatchActionState(Protocol):
    """State needed to apply actions from a reviewed batch file."""

    flow_state: FlowState
    file_path: str


def apply_batch_line_action(
    state: FileReviewBatchActionState,
    action: str,
    line_ids: str,
) -> None:
    """Apply a line action from a batch-backed file review."""
    if state.flow_state.target.role is not LocationRole.STAGING_AREA:
        _print_batch_to_batch_error()
        return

    if action == "i":
        from ...commands.include_from import command_include_from_batch

        command_include_from_batch(
            state.flow_state.source.batch_name,
            line_ids=line_ids,
            file=state.file_path,
        )
        return

    from ...commands.discard_from import command_discard_from_batch

    command_discard_from_batch(
        state.flow_state.source.batch_name,
        line_ids=line_ids,
        file=state.file_path,
    )


def apply_batch_replacement_action(
    state: FileReviewBatchActionState,
    line_ids: str,
    replacement_text: str,
) -> None:
    """Apply a replacement action from a batch-backed file review."""
    if state.flow_state.target.role is not LocationRole.STAGING_AREA:
        _print_batch_to_batch_error()
        return

    from ...commands.include_from import command_include_from_batch

    command_include_from_batch(
        state.flow_state.source.batch_name,
        line_ids=line_ids,
        file=state.file_path,
        replacement_text=replacement_text,
    )


def apply_batch_file_action(
    state: FileReviewBatchActionState,
    action: str,
) -> None:
    """Apply a whole-file action from a batch-backed file review."""
    if state.flow_state.target.role is not LocationRole.STAGING_AREA:
        _print_batch_to_batch_error()
        return

    if action == "I":
        from ...commands.include_from import command_include_from_batch

        command_include_from_batch(
            state.flow_state.source.batch_name,
            file=state.file_path,
        )
        return

    from ...commands.discard_from import command_discard_from_batch

    command_discard_from_batch(
        state.flow_state.source.batch_name,
        file=state.file_path,
    )


def _print_batch_to_batch_error() -> None:
    print(
        _("Batch-to-batch transfers not yet supported. Target must be staging."),
        file=sys.stderr,
    )
