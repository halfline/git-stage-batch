"""Include command implementation."""

from __future__ import annotations

from ..batch.state.batch_names import validate_batch_name
from ..core.replacement import (
    ReplacementPayload,
)
from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from ..data.line_id_files import read_line_ids_file
from ..data.line_state import load_line_changes_from_state
from ..data.selected_change.clear_reasons import (
    refuse_bare_action_after_auto_advance_disabled,
    refuse_bare_action_after_file_list,
)
from ..data.file_review.records import FileReviewAction
from ..data.file_review.action_refusals import (
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
)
from ..data.file_review.action_scope import (
    resolve_live_line_action_scope,
    resolve_live_to_batch_action_scope,
)
from ..data.session import require_session_started
from ..utils.git_repository import require_git_repository
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_processed_skip_ids_file_path,
)
from .selection import include_to_batch_action as _include_to_batch_action
from .selection import include_line_action as _include_line_action
from .selection import (
    include_line_replacement_action as _include_line_replacement_action,
)
from .selection import selected_change_staging as _selected_change_staging
from .file_scope import include_file as _file_scope_include_file
from .file_scope import include_file_replacement as _file_scope_include_file_replacement


def command_include(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Include (stage) the selected hunk or binary file."""

    log_journal("command_include_start", quiet=quiet)

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if refuse_live_action_for_batch_selection(FileReviewAction.INCLUDE):
        return
    if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.INCLUDE):
        return
    refuse_bare_action_after_file_list("include")
    refuse_bare_action_after_auto_advance_disabled("include")

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        command_include_file("", auto_advance=auto_advance)
        return

    if read_selected_change_kind() == SelectedChangeKind.HUNK:
        skipped_ids = read_line_ids_file(get_processed_skip_ids_file_path())
        line_changes = load_line_changes_from_state()
        if skipped_ids and line_changes is not None:
            remaining_ids = line_changes.changed_line_ids()
            if remaining_ids:
                _include_line_action.include_live_line_selection(
                    ",".join(str(line_id) for line_id in remaining_ids),
                    review_state=None,
                    auto_advance=auto_advance,
                    quiet=quiet,
                    operation="include",
                )
                return

    _selected_change_staging.include_selected_change(
        quiet=quiet,
        auto_advance=auto_advance,
    )


def command_include_file(
    file: str,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Include (stage) all hunks from the specified file.

    Args:
        file: File path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If explicit path, uses that file.
        quiet: Suppress per-file status output while preserving selection state.
        advance: When quiet, advance the selection after staging this file.
        auto_advance: Whether to select the next hunk after this action.

    Returns:
        Number of hunks staged from the requested file.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.INCLUDE):
            return 0
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.INCLUDE):
            return 0
        refuse_bare_action_after_file_list("include --file")
        refuse_bare_action_after_auto_advance_disabled("include --file")

    return _file_scope_include_file.include_file_changes(
        file,
        quiet=quiet,
        advance=advance,
        auto_advance=auto_advance,
    )


def command_include_file_as(
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage full-file replacement text for a live file-scoped selection."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    if file is None or file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.INCLUDE):
            return
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.INCLUDE):
            return
        refuse_bare_action_after_file_list("include --file --as")
        refuse_bare_action_after_auto_advance_disabled("include --file --as")

    _file_scope_include_file_replacement.include_file_as_replacement(
        replacement_text,
        file,
        auto_advance=auto_advance,
    )


def command_include_line(
    line_id_specification: str,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage only the specified lines to the index.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
        file: Optional file path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.INCLUDE,
        action_command=f"include --line {line_id_specification}",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state

    _include_line_action.include_live_line_selection(
        line_id_specification,
        file,
        review_state=review_state,
        auto_advance=auto_advance,
    )


def command_include_line_as(
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    no_edge_overlap: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Stage a replacement for one contiguous selected line span and mask it."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.INCLUDE,
        action_command=f"include --line {line_id_specification} --as",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state

    _include_line_replacement_action.include_live_line_replacement(
        line_id_specification,
        replacement_text,
        file,
        review_state=review_state,
        no_edge_overlap=no_edge_overlap,
        auto_advance=auto_advance,
    )


def command_include_to_batch(
    batch_name: str,
    line_ids: str | None = None,
    file: str | None = None,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save selected changes to batch instead of staging.

    Args:
        batch_name: Name of batch to save to
        line_ids: Optional line IDs to include
        file: Optional file path for file-scoped operations.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
        quiet: Suppress output
        auto_advance: Whether to select the next hunk after this action.
    """
    require_git_repository()
    validate_batch_name(batch_name)
    ensure_state_directory_exists()
    original_file_scope = file
    scope_resolution = resolve_live_to_batch_action_scope(
        FileReviewAction.INCLUDE_TO_BATCH,
        command_name="include",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
    )
    if scope_resolution.should_stop:
        return
    file = scope_resolution.file
    review_state = scope_resolution.review_state
    _include_to_batch_action.execute_include_to_batch_action(
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        original_file_scope=original_file_scope,
        review_state=review_state,
        quiet=quiet,
        auto_advance=auto_advance,
    )
