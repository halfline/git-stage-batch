"""Skip command implementation."""

from __future__ import annotations

from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
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
)
from ..data.session import require_session_started
from ..utils.git_repository import require_git_repository
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
)
from .file_scope import skip_file as _file_scope_skip_file
from .selection import skip_line_selection as _skip_line_selection
from .selection import selected_change_skipping as _selected_change_skipping


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
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.SKIP,
        action_command=f"skip --line {line_id_specification}",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    _skip_line_selection.skip_line_selection(
        line_id_specification,
        file=file,
        review_state=scope_resolution.review_state,
        auto_advance=auto_advance,
    )
