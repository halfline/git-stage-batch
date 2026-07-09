"""Skip command implementation."""

from __future__ import annotations

from dataclasses import replace
import json
import sys

from ..batch.selection import require_line_selection_in_view
from ..core.line_selection import parse_line_selection
from ..data.line_id_files import read_line_ids_file, write_line_ids_file
from ..data.selected_change.loading import (
    require_selected_hunk,
)
from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from ..data.selected_change.paths import get_selected_change_file_path
from ..data.selected_change.clear_reasons import (
    refuse_bare_action_after_auto_advance_disabled,
    refuse_bare_action_after_file_list,
)
from ..data.file_review.records import FileReviewAction
from ..data.file_review.action_scope import (
    finish_review_scoped_line_action,
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
    resolve_live_line_action_scope,
)
from ..data.file_hunk_display import (
    cache_unstaged_file_as_single_hunk,
    render_unstaged_file_as_single_hunk,
)
from ..data.file_tracking import auto_add_untracked_files
from ..data.line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from ..data.progress import (
    record_hunk_skipped,
)
from ..data.session import require_session_started
from ..data.undo import undo_checkpoint
from ..exceptions import NoMoreHunks, exit_with_error
from ..i18n import _
from ..output.hunk import (
    print_line_level_changes,
    print_remaining_line_changes_header,
)
from ..utils.file_io import (
    append_lines_to_file,
    read_text_file_contents,
    write_text_file_contents,
)
from ..utils.git_repository import require_git_repository
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
    get_processed_skip_ids_file_path,
)
from .file_scope import skip_file as _file_scope_skip_file
from .selection import selected_change_skipping as _selected_change_skipping
from .selection.action_completion import finish_selected_change_action


def command_skip(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Skip the selected hunk or binary file without staging it."""
    log_journal("command_skip_start", quiet=quiet)

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if refuse_live_action_for_batch_selection(FileReviewAction.SKIP):
        return
    if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.SKIP):
        return
    refuse_bare_action_after_file_list("skip")
    refuse_bare_action_after_auto_advance_disabled("skip")

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        command_skip_file("", auto_advance=auto_advance)
        return

    _selected_change_skipping.skip_selected_change(
        quiet=quiet,
        auto_advance=auto_advance,
    )


def command_skip_file(
    file: str = "",
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Skip all remaining hunks from the specified file.

    Args:
        file: File path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If explicit path, uses that file.

        quiet: Suppress per-file status output while preserving selection state.
        advance: When quiet, advance the selection after skipping this file.

    Returns:
        Number of hunks skipped from the requested file.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.SKIP):
            return 0
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.SKIP):
            return 0
        refuse_bare_action_after_file_list("skip --file")
        refuse_bare_action_after_auto_advance_disabled("skip --file")

    return _file_scope_skip_file.skip_file_changes(
        file,
        quiet=quiet,
        advance=advance,
        auto_advance=auto_advance,
    )


def command_skip_line(
    line_id_specification: str,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Mark only the specified lines as skipped.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    target_file = None
    reuse_selected_file_view = False
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.SKIP,
        action_command=f"skip --line {line_id_specification}",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state
    if file is None:
        require_selected_hunk()
    else:
        if file == "":
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file
        auto_add_untracked_files([target_file])

        reuse_selected_file_view = (
            read_selected_change_kind() == SelectedChangeKind.FILE
            and get_selected_change_file_path() == target_file
        )
        if not reuse_selected_file_view and render_unstaged_file_as_single_hunk(target_file) is None:
            exit_with_error(_("No changes in file '{file}'.").format(file=target_file))

    requested_ids = parse_line_selection(line_id_specification)
    operation_parts = ["skip", "--line", line_id_specification]
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        if file is not None:
            if not reuse_selected_file_view:
                if cache_unstaged_file_as_single_hunk(target_file) is None:
                    exit_with_error(_("No changes in file '{file}'.").format(file=target_file))
            require_selected_hunk()

        line_changes = load_line_changes_from_state()
        if line_changes is None:
            raise NoMoreHunks()
        require_line_selection_in_view(
            line_changes,
            set(requested_ids),
            line_id_specification=line_id_specification,
        )

        already_skipped_ids = (
            set(read_line_ids_file(get_processed_skip_ids_file_path()))
            if file is None or reuse_selected_file_view else
            set()
        )
        combined_skip_ids = already_skipped_ids | set(requested_ids)
        # Update processed skip IDs
        write_line_ids_file(get_processed_skip_ids_file_path(), combined_skip_ids)

        visible_changed_ids = [
            changed_id
            for changed_id in line_changes.changed_line_ids()
            if changed_id not in combined_skip_ids
        ]

        if not visible_changed_ids:
            if read_selected_change_kind() == SelectedChangeKind.FILE:
                print(_("✓ Skipped line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)
                finish_review_scoped_line_action(review_state)
                command_skip_file("", auto_advance=auto_advance)
                return

            patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
            blocklist_path = get_block_list_file_path()
            append_lines_to_file(blocklist_path, [patch_hash])
            record_hunk_skipped(line_changes, patch_hash)
            print(_("✓ Skipped line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)
            finish_review_scoped_line_action(review_state)
            finish_selected_change_action(
                quiet=False,
                auto_advance=auto_advance,
            )
            return

        filtered_lines = [
            replace(line_entry, id=None)
            if line_entry.id in combined_skip_ids
            else line_entry
            for line_entry in line_changes.lines
        ]
        filtered_line_changes = replace(line_changes, lines=filtered_lines)
        write_text_file_contents(
            get_line_changes_json_file_path(),
            json.dumps(
                convert_line_changes_to_serializable_dict(filtered_line_changes),
                ensure_ascii=False,
                indent=0,
            ),
        )

    print(_("✓ Skipped line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)
    print_remaining_line_changes_header(filtered_line_changes.path)
    print_line_level_changes(filtered_line_changes)
    finish_review_scoped_line_action(review_state)
