"""Include command implementation."""

from __future__ import annotations

from ..core.replacement import (
    ReplacementPayload,
)
from ..core.models import BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ..data.selected_change.loading import (
    load_selected_change,
)
from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
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
    resolve_live_to_batch_action_scope,
)
from ..data.session import require_session_started
from ..data.undo import undo_checkpoint
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.git_repository import require_git_repository
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
)
from .selection import include_line_action as _include_line_action
from .selection import include_line_batching as _include_line_batching
from .selection import (
    include_line_replacement_action as _include_line_replacement_action,
)
from .selection import selected_change_batch_staging as _selected_change_batch_staging
from .selection import selected_change_staging as _selected_change_staging
from .selection import whole_file_batch_staging as _whole_file_batch_staging
from .file_scope import include_file as _file_scope_include_file
from .file_scope import include_file_replacement as _file_scope_include_file_replacement
from .file_scope import include_file_to_batch as _file_scope_include_file_to_batch
from .file_scope.target_path import require_file_scope_target_path


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
        return 0
    if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.INCLUDE):
        return 0
    refuse_bare_action_after_file_list("include")
    refuse_bare_action_after_auto_advance_disabled("include")

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        command_include_file("", auto_advance=auto_advance)
        return 0

    return _selected_change_staging.include_selected_change(
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
    operation_parts = ["include", "--to", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.RENAME
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, RenameChange):
                exit_with_error(
                    _(
                        "Cannot include rename '{old} -> {new}' to a batch yet. "
                        "Stage, skip, or discard the rename first."
                    ).format(
                        old=selected_change.old_path,
                        new=selected_change.new_path,
                    )
                )
        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.GITLINK
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, GitlinkChange):
                _whole_file_batch_staging.include_gitlink_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            else:
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
        elif (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.DELETION
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, TextFileDeletionChange):
                _whole_file_batch_staging.include_text_deletion_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            else:
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
        elif (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.BINARY
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, BinaryFileChange):
                _whole_file_batch_staging.include_binary_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            else:
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
        elif file is not None:
            # File-scoped operation
            target_file = require_file_scope_target_path(file)

            if line_ids is None:
                # --file without --line: include entire file
                _file_scope_include_file_to_batch.include_file_to_batch(
                    batch_name,
                    target_file,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                # --file with --line: include specific lines from file
                _include_line_batching.include_file_lines_to_batch(
                    batch_name,
                    target_file,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
        else:
            # Hunk-scoped operation (selected behavior)
            if line_ids is not None:
                _include_line_batching.include_selected_lines_to_batch(
                    batch_name,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                # Include entire selected hunk
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
    if original_file_scope in (None, "") and line_ids is not None:
        finish_review_scoped_line_action(review_state)
