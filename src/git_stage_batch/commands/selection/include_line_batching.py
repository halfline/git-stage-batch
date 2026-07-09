"""Include line selections to batches."""

from __future__ import annotations

import sys

from ...batch.source_annotation import annotate_with_batch_source
from ...data.line_state import load_line_changes_from_state
from ...data.selected_change.loading import require_selected_hunk
from ...exceptions import exit_with_error
from ...i18n import _
from . import batch_line_selection as _batch_line_selection
from . import batch_line_updates as _batch_line_updates
from . import include_file_selection as _include_file_selection
from . import include_line_selection as _include_line_selection
from .action_completion import finish_selected_change_action
from .selected_hunk_refresh import recalculate_selected_hunk_for_command


def include_file_lines_to_batch(
    batch_name: str,
    file_path: str,
    line_id_specification: str,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Include specific lines from a file to batch."""
    cached_lines = _include_file_selection.load_explicit_file_selection(file_path)
    line_changes = annotate_with_batch_source(file_path, cached_lines)
    _include_line_selection.record_baseline_references_for_additions(line_changes)

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
        return

    _batch_line_updates.add_selected_lines_to_batch(
        batch_name=batch_name,
        file_path=file_path,
        selected_lines=selection.selected_lines,
        stale_source_action=_("Cannot include lines to batch"),
        snapshot_untracked=True,
    )

    if not quiet:
        print(
            _("Included line(s) from file '{file}' to batch '{batch}': {lines}").format(
                file=file_path,
                batch=batch_name,
                lines=line_id_specification,
            ),
            file=sys.stderr,
        )

    finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)


def include_selected_lines_to_batch(
    batch_name: str,
    line_id_specification: str,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save selected hunk lines to a batch."""
    require_selected_hunk()

    line_changes = load_line_changes_from_state()
    _include_line_selection.record_baseline_references_for_additions(line_changes)
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

    _batch_line_updates.add_selected_lines_to_batch(
        batch_name=batch_name,
        file_path=line_changes.path,
        selected_lines=selection.selected_lines,
        stale_source_action=_("Cannot include lines to batch"),
    )

    if not quiet:
        print(
            _("✓ Included line(s) to batch '{name}': {lines}").format(
                name=batch_name,
                lines=line_id_specification,
            ),
            file=sys.stderr,
        )

    recalculate_selected_hunk_for_command(line_changes.path, auto_advance=auto_advance)
