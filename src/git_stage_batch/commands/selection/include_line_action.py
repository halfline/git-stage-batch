"""Live include line action orchestration."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...batch.selection import require_line_selection_in_view
from ...core.buffer import LineBuffer, buffer_matches
from ...core.line_selection import parse_line_selection
from ...data.file_review.action_scope import finish_review_scoped_line_action
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.line_id_files import read_line_ids_file, write_line_ids_file
from ...utils.repository_buffers import load_git_object_as_buffer
from ...data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
    restore_selected_change_state,
)
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.content_buffers import build_target_index_buffer_from_lines
from ...utils.journal import log_journal
from ...utils.paths import (
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_working_tree_snapshot_file_path,
)
from . import include_line_selection as _include_line_selection
from . import replacement_selection
from .selected_hunk_refresh import refresh_selected_hunk_after_line_action


def include_live_line_selection(
    line_id_specification: str,
    file: str | None = None,
    *,
    review_state,
    auto_advance: bool | None = None,
) -> None:
    """Stage selected lines from the live working-tree view."""
    operation_parts = ["include", "--line", line_id_specification]
    if file is not None:
        operation_parts.extend(["--file", file])

    target_file = file if file not in (None, "") else get_selected_change_file_path()
    with (
        undo_checkpoint(
            " ".join(operation_parts),
            worktree_paths=[target_file] if target_file is not None else [],
        ),
        ExitStack() as selected_state_stack,
    ):
        selection_context = _include_line_selection.load_include_line_selection_context(
            file,
            selected_state_stack,
        )
        line_changes = selection_context.line_changes

        requested_ids = parse_line_selection(line_id_specification)
        require_line_selection_in_view(
            line_changes,
            set(requested_ids),
            line_id_specification=line_id_specification,
        )
        if selection_context.reset_processed_include_ids:
            already_included_ids = set()
        else:
            already_included_ids = set(
                read_line_ids_file(get_processed_include_ids_file_path())
            )
        combined_include_ids = already_included_ids | set(requested_ids)

        current_index_buffer = load_git_object_as_buffer(f":{line_changes.path}")
        if current_index_buffer is None:
            current_index_buffer = LineBuffer.from_bytes(b"")

        with (
            LineBuffer.from_path(get_index_snapshot_file_path()) as hunk_base_lines,
            LineBuffer.from_path(
                get_working_tree_snapshot_file_path()
            ) as hunk_source_lines,
            current_index_buffer as current_index_lines,
        ):
            selected_change_kind = read_selected_change_kind()
            if selected_change_kind == SelectedChangeKind.FILE:
                leading_replacement_addition_error = (
                    replacement_selection.build_leading_replacement_addition_selection_error(
                        line_changes,
                        combined_include_ids,
                    )
                )
                if leading_replacement_addition_error is not None:
                    exit_with_error(leading_replacement_addition_error)

                partial_structural_run_error = (
                    replacement_selection.build_partial_structural_run_selection_error(
                        line_changes,
                        combined_include_ids,
                        hunk_base_lines=hunk_base_lines,
                        hunk_source_lines=hunk_source_lines,
                    )
                )
                if partial_structural_run_error is not None:
                    exit_with_error(partial_structural_run_error)

            transient_result = (
                _include_line_selection.try_build_index_content_via_transient_batch(
                    line_changes=line_changes,
                    selected_display_ids=set(combined_include_ids),
                    current_index_lines=current_index_lines,
                    hunk_base_lines=hunk_base_lines,
                    hunk_source_lines=hunk_source_lines,
                )
            )
            if (
                transient_result.buffer is None
                and transient_result.failure_reason
                == _include_line_selection.TransientIncludeFailureReason.INDEX_MERGE_FAILED
                and buffer_matches(current_index_lines, hunk_base_lines)
            ):
                transient_result = _include_line_selection.TransientIncludeResult.success(
                    build_target_index_buffer_from_lines(
                        line_changes,
                        set(combined_include_ids),
                        hunk_base_lines,
                        base_has_trailing_newline=(
                            _include_line_selection.line_sequence_ends_with_lf(
                                hunk_base_lines
                            )
                        ),
                    )
                )
        if transient_result.buffer is not None:
            log_journal(
                "include_line_transient_batch_staging_used",
                file_path=line_changes.path,
                selected_ids=sorted(combined_include_ids),
            )
            target_index_buffer_context = transient_result.buffer
        else:
            failure_reason = (
                transient_result.failure_reason
                or _include_line_selection.TransientIncludeFailureReason.PREPARATION_FAILED
            )
            log_journal(
                "include_line_transient_batch_staging_declined",
                file_path=line_changes.path,
                selected_ids=sorted(combined_include_ids),
                reason=failure_reason.value,
                detail=transient_result.failure_detail,
            )
            exit_with_error(
                _include_line_selection.transient_include_failure_message(
                    reason=failure_reason,
                    line_id_specification=line_id_specification,
                    file_path=line_changes.path,
                )
            )

        with target_index_buffer_context as target_index_buffer:
            _include_line_selection.stage_live_line_target_buffer(
                line_changes.path,
                target_index_buffer,
            )

        if selection_context.preserve_selected_state:
            assert selection_context.saved_selected_state is not None
            restore_selected_change_state(selection_context.saved_selected_state)
        else:
            write_line_ids_file(
                get_processed_include_ids_file_path(),
                combined_include_ids,
            )
            print(
                _("✓ Included line(s): {lines} from {file}").format(
                    lines=line_id_specification,
                    file=line_changes.path,
                ),
                file=sys.stderr,
            )
            refresh_selected_hunk_after_line_action(
                line_changes.path,
                auto_advance=auto_advance,
            )
        finish_review_scoped_line_action(review_state, file_path=line_changes.path)
    if selection_context.preserve_selected_state:
        print(
            _("✓ Included line(s): {lines} from {file}").format(
                lines=line_id_specification,
                file=line_changes.path,
            ),
            file=sys.stderr,
        )
