"""Discard line selections to batches and update the working tree."""

from __future__ import annotations

import os
import sys

from ...batch.source.annotation import annotate_with_batch_source
from ...core.buffer import LineBuffer, buffer_ends_with_lf, write_buffer_to_path
from ...core.replacement import ReplacementPayload
from ...data.line_state import load_line_changes_from_state
from ...utils.repository_buffers import load_working_tree_file_as_buffer
from ...data.selected_change.loading import require_selected_hunk
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.content_buffers import build_target_working_tree_buffer_from_lines
from ...utils.git_repository import get_git_repository_root_path
from ...utils.journal import log_journal
from . import batch_line_selection as _batch_line_selection
from . import batch_line_updates as _batch_line_updates
from . import discard_file_selection as _discard_file_selection
from . import discard_line_replacement as _discard_line_replacement
from .action_completion import finish_selected_change_action
from .selected_hunk_refresh import recalculate_selected_hunk_for_command


def discard_lines_as_to_batch(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    *,
    no_edge_overlap: bool = False,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Persist replacement text to batch and discard original selected lines."""
    target_working_buffer: LineBuffer | None = None
    replacement = None
    try:
        with _discard_line_replacement.prepare_discard_line_replacement_selection(
            line_id_specification,
            replacement_text,
            no_edge_overlap=no_edge_overlap,
        ) as replacement:
            _discard_line_replacement.add_discard_line_replacement_to_batch(
                batch_name,
                replacement,
            )

            target_working_buffer = (
                _discard_line_replacement.build_discard_line_replacement_target_buffer(
                    replacement
                )
            )

    except Exception:
        if target_working_buffer is not None:
            target_working_buffer.close()
        raise

    assert target_working_buffer is not None
    assert replacement is not None
    with target_working_buffer:
        write_buffer_to_path(replacement.working_file_path, target_working_buffer)

    if not quiet:
        print(
            _("✓ Discarded line(s) as replacement to batch '{name}': {lines}").format(
                name=batch_name,
                lines=line_id_specification,
            ),
            file=sys.stderr,
        )

    recalculate_selected_hunk_for_command(
        replacement.file_path,
        auto_advance=auto_advance,
    )


def discard_file_lines_to_batch(
    batch_name: str,
    file_path: str,
    line_id_specification: str,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> int:
    """Discard specific lines from a file to batch."""
    cached_lines = _discard_file_selection.load_explicit_file_selection(file_path)
    line_changes = annotate_with_batch_source(file_path, cached_lines)

    selection = _batch_line_selection.select_lines_for_batch_action(
        line_changes,
        line_id_specification,
    )

    if not selection.selected_lines:
        if not quiet:
            print(
                _("No lines match the specified IDs in file '{file}'.").format(
                    file=file_path,
                ),
                file=sys.stderr,
            )
        return 0

    replacement_line_runs = (
        _discard_line_replacement.derive_live_replacement_line_runs(file_path)
    )

    _batch_line_updates.add_selected_lines_to_batch(
        batch_name=batch_name,
        file_path=file_path,
        selected_lines=selection.selected_lines,
        stale_source_action=_("Cannot discard lines to batch"),
        hunk_lines=line_changes.lines,
        replacement_line_runs=replacement_line_runs,
        snapshot_untracked=True,
    )

    working_file_path = get_git_repository_root_path() / file_path
    if not os.path.lexists(working_file_path):
        exit_with_error(_("File not found in working tree: {file}").format(file=file_path))

    with load_working_tree_file_as_buffer(file_path) as working_lines:
        target_working_buffer = build_target_working_tree_buffer_from_lines(
            line_changes,
            selection.requested_ids,
            working_lines,
            working_has_trailing_newline=buffer_ends_with_lf(working_lines),
        )

    with target_working_buffer:
        write_buffer_to_path(working_file_path, target_working_buffer)

    if not quiet:
        print(
            _("Discarded line(s) from file '{file}' to batch '{batch}': {lines}").format(
                file=file_path,
                batch=batch_name,
                lines=line_id_specification,
            ),
            file=sys.stderr,
        )

    finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
    return 1


def discard_selected_lines_to_batch(
    batch_name: str,
    line_id_specification: str,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> int:
    """Save specific selected lines to batch and discard them."""
    log_journal(
        "discard_lines_to_batch_start",
        batch_name=batch_name,
        line_ids=line_id_specification,
        quiet=quiet,
    )

    require_selected_hunk()

    line_changes = load_line_changes_from_state()
    selection = _batch_line_selection.select_lines_for_batch_action(
        line_changes,
        line_id_specification,
    )

    if not selection.selected_lines:
        exit_with_error(
            _("No matching lines found for selection: {ids}").format(
                ids=line_id_specification,
            )
        )

    replacement_line_runs = (
        _discard_line_replacement.derive_live_replacement_line_runs(
            line_changes.path
        )
    )

    _batch_line_updates.add_selected_lines_to_batch(
        batch_name=batch_name,
        file_path=line_changes.path,
        selected_lines=selection.selected_lines,
        stale_source_action=_("Cannot discard lines to batch"),
        hunk_lines=line_changes.lines,
        replacement_line_runs=replacement_line_runs,
        snapshot_untracked=True,
        before_add=lambda: log_journal(
            "discard_lines_to_batch_before_add",
            batch_name=batch_name,
            file_path=line_changes.path,
        ),
    )

    log_journal(
        "discard_lines_to_batch_after_add",
        batch_name=batch_name,
        file_path=line_changes.path,
    )

    working_file_path = get_git_repository_root_path() / line_changes.path
    if not os.path.lexists(working_file_path):
        exit_with_error(
            _("File not found in working tree: {file}").format(
                file=line_changes.path,
            )
        )

    with load_working_tree_file_as_buffer(line_changes.path) as working_lines:
        target_working_buffer = build_target_working_tree_buffer_from_lines(
            line_changes,
            selection.requested_ids,
            working_lines,
            working_has_trailing_newline=buffer_ends_with_lf(working_lines),
        )

    log_journal("discard_lines_to_batch_before_write", file_path=str(working_file_path))
    with target_working_buffer:
        write_buffer_to_path(working_file_path, target_working_buffer)
    log_journal("discard_lines_to_batch_after_write", file_path=str(working_file_path))

    if not quiet:
        print(
            _("✓ Discarded line(s) to batch '{name}': {lines}").format(
                name=batch_name,
                lines=line_id_specification,
            ),
            file=sys.stderr,
        )

    recalculate_selected_hunk_for_command(
        line_changes.path,
        auto_advance=auto_advance,
    )

    log_journal(
        "discard_lines_to_batch_success",
        batch_name=batch_name,
        line_ids=line_id_specification,
        file_path=line_changes.path,
    )
    return 1
