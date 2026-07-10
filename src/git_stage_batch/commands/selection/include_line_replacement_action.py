"""Live include line replacement action orchestration."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.file_review.action_scope import finish_review_scoped_line_action
from ...data.line_id_files import write_line_ids_file
from ...data.selected_change.store import restore_selected_change_state
from ...data.undo_checkpoints import undo_checkpoint
from ...i18n import _
from ...utils.paths import get_processed_include_ids_file_path
from . import include_line_replacement as _include_line_replacement
from .selected_hunk_refresh import refresh_selected_hunk_after_line_action


def include_live_line_replacement(
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    review_state,
    no_edge_overlap: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Stage replacement text for selected lines from the live view."""
    replacement_payload = coerce_replacement_payload(replacement_text)
    operation_parts = [
        "include",
        "--line",
        line_id_specification,
        "--as",
        replacement_payload.display_text or "<stdin>",
    ]
    if no_edge_overlap:
        operation_parts.append("--no-edge-overlap")
    if file is not None:
        operation_parts.extend(["--file", file])

    replacement_file_context = None
    with undo_checkpoint(" ".join(operation_parts)), ExitStack() as selected_state_stack:
        if file is None:
            replacement_context = (
                _include_line_replacement.prepare_pathless_include_line_replacement(
                    line_id_specification
                )
            )
            line_changes = replacement_context.display_line_changes
            with (
                replacement_context.base_buffer as hunk_base_lines,
                replacement_context.source_buffer as hunk_source_lines,
            ):
                _include_line_replacement.apply_include_line_replacement(
                    replacement_context.replacement_line_changes,
                    line_id_specification=replacement_context.line_id_specification,
                    replacement_text=replacement_text,
                    hunk_base_lines=hunk_base_lines,
                    hunk_source_lines=hunk_source_lines,
                    trim_unchanged_edge_anchors=not no_edge_overlap,
                )

            write_line_ids_file(get_processed_include_ids_file_path(), set())
            print(
                _("✓ Included line(s) as replacement: {lines} from {file}").format(
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
        else:
            replacement_file_context = (
                _include_line_replacement.prepare_file_include_line_replacement(
                    file,
                    selected_state_stack,
                )
            )
            with (
                replacement_file_context.base_buffer as hunk_base_lines,
                replacement_file_context.source_buffer as hunk_source_lines,
            ):
                _include_line_replacement.apply_include_line_replacement(
                    replacement_file_context.line_changes,
                    line_id_specification=line_id_specification,
                    replacement_text=replacement_text,
                    hunk_base_lines=hunk_base_lines,
                    hunk_source_lines=hunk_source_lines,
                    trim_unchanged_edge_anchors=not no_edge_overlap,
                )

            if replacement_file_context.preserve_selected_state:
                assert replacement_file_context.saved_selected_state is not None
                restore_selected_change_state(
                    replacement_file_context.saved_selected_state
                )
            else:
                write_line_ids_file(get_processed_include_ids_file_path(), set())
                print(
                    _("✓ Included line(s) as replacement: {lines} from {file}").format(
                        lines=line_id_specification,
                        file=replacement_file_context.target_file,
                    ),
                    file=sys.stderr,
                )
                refresh_selected_hunk_after_line_action(
                    replacement_file_context.target_file,
                    auto_advance=auto_advance,
                )
            finish_review_scoped_line_action(
                review_state,
                file_path=replacement_file_context.target_file,
            )

    if (
        replacement_file_context is not None
        and replacement_file_context.preserve_selected_state
    ):
        print(
            _("✓ Included line(s) as replacement: {lines} from {file}").format(
                lines=line_id_specification,
                file=replacement_file_context.target_file,
            ),
            file=sys.stderr,
        )
