"""Batch updates for selected line ownership."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import ExitStack

from ...batch.state.lifecycle import create_batch
from ...batch.ownership_update import acquire_batch_ownership_update_for_selection
from ...batch.state.query import read_batch_metadata
from ...batch.text_file_storage import add_file_to_batch
from ...batch.state.batch_names import batch_exists
from ...data.file_modes import detect_file_mode
from ...data.session import snapshot_file_if_untracked
from ...exceptions import exit_with_error
from ...i18n import _


def add_selected_lines_to_batch(
    *,
    batch_name: str,
    file_path: str,
    selected_lines: Sequence,
    stale_source_action: str,
    hunk_lines: Sequence | None = None,
    replacement_line_runs=None,
    snapshot_untracked: bool = False,
    before_add: Callable[[], None] | None = None,
) -> None:
    """Persist selected lines into batch ownership for one file."""
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    file_mode = detect_file_mode(file_path)
    metadata = read_batch_metadata(batch_name)
    file_metadata = metadata.get("files", {}).get(file_path)

    with ExitStack() as ownership_stack:
        try:
            update = ownership_stack.enter_context(
                acquire_batch_ownership_update_for_selection(
                    batch_name=batch_name,
                    file_path=file_path,
                    file_metadata=file_metadata,
                    selected_lines=selected_lines,
                    hunk_lines=hunk_lines,
                    replacement_line_runs=replacement_line_runs,
                )
            )
        except ValueError as e:
            exit_with_error(
                _(
                    "{action}: batch source is stale and remapping failed.\n"
                    "File: {file}\n"
                    "Batch: {batch}\n"
                    "Error: {error}"
                ).format(
                    action=stale_source_action,
                    file=file_path,
                    batch=batch_name,
                    error=str(e),
                )
            )

        if snapshot_untracked:
            snapshot_file_if_untracked(file_path)
        if before_add is not None:
            before_add()
        add_file_to_batch(
            batch_name,
            file_path,
            update.ownership_after,
            file_mode,
            batch_source_commit=update.batch_source_commit,
        )
