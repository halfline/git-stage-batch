"""Batch updates for selected line ownership."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import ExitStack

from ...batch.ownership.replacement_line_runs import (
    stream_replacement_line_runs_from_lines,
)
from ...batch.state.lifecycle import create_batch
from ...batch.ownership_update import acquire_batch_ownership_update_for_selection
from ...batch.state.query import read_batch_metadata
from ...batch.state.validation import get_validated_baseline_commit
from ...batch.text_file_storage import add_file_to_batch
from ...batch.state.batch_names import batch_exists
from ...data.file_modes import detect_file_mode
from ...data.session import snapshot_file_if_untracked
from ...data.selected_change.snapshots import (
    load_selected_file_comparison_base_buffer,
)
from ...exceptions import exit_with_error
from ...git_paths import display_path
from ...i18n import _
from ...core.models import LineEntry
from ...utils.repository_buffers import (
    load_working_tree_file_as_buffer,
    read_git_object_buffer_or_empty,
)


def add_selected_lines_to_batch(
    *,
    batch_name: str,
    file_path: str,
    selected_lines: Sequence[LineEntry],
    stale_source_action: str,
    hunk_lines: Sequence[LineEntry] | None = None,
    snapshot_untracked: bool = False,
    before_add: Callable[[], None] | None = None,
) -> None:
    """Persist selected lines into batch ownership for one file."""
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    file_mode = detect_file_mode(file_path)
    metadata = read_batch_metadata(batch_name)
    file_metadata = metadata.get("files", {}).get(file_path)
    baseline_commit = get_validated_baseline_commit(batch_name)
    has_replacement_rows = (
        hunk_lines is not None
        and any(getattr(line, "kind", None) == "+" for line in hunk_lines)
        and any(getattr(line, "kind", None) == "-" for line in hunk_lines)
    )

    with ExitStack() as ownership_stack:
        replacement_origin_source_lines = (
            ownership_stack.enter_context(
                read_git_object_buffer_or_empty(f"HEAD:{file_path}")
            )
            if has_replacement_rows
            else None
        )
        working_source_lines = (
            ownership_stack.enter_context(
                load_working_tree_file_as_buffer(file_path)
            )
            if has_replacement_rows
            else None
        )
        try:
            reference_source_lines = ownership_stack.enter_context(
                load_selected_file_comparison_base_buffer(file_path)
            )
            replacement_line_runs = (
                stream_replacement_line_runs_from_lines(
                    old_file_lines=reference_source_lines,
                    new_file_lines=working_source_lines,
                )
                if working_source_lines is not None
                else None
            )
            replacement_origin_line_runs = (
                stream_replacement_line_runs_from_lines(
                    old_file_lines=replacement_origin_source_lines,
                    new_file_lines=working_source_lines,
                )
                if (
                    replacement_origin_source_lines is not None
                    and working_source_lines is not None
                )
                else None
            )
            update = ownership_stack.enter_context(
                acquire_batch_ownership_update_for_selection(
                    batch_name=batch_name,
                    file_path=file_path,
                    file_metadata=file_metadata,
                    selected_lines=list(selected_lines),
                    hunk_lines=hunk_lines,
                    replacement_line_runs=replacement_line_runs,
                    replacement_origin_line_runs=(
                        replacement_origin_line_runs
                    ),
                    reference_source_lines=reference_source_lines,
                    batch_baseline_commit=baseline_commit,
                    replacement_origin_source_lines=(
                        replacement_origin_source_lines
                    ),
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
                    file=display_path(file_path),
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
