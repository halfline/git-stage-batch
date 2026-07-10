"""Discard command implementation."""

from __future__ import annotations

import sys

from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
)
from ..core.models import BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ..data.selected_change.loading import (
    load_selected_change,
    require_selected_hunk,
)
from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from ..data.selected_change.paths import get_selected_change_file_path
from ..data.selected_change.clear_reasons import (
    refuse_bare_action_after_auto_advance_disabled,
    refuse_bare_action_after_file_list,
)
from ..data.file_hunk_display import cache_unstaged_file_as_single_hunk
from ..data.file_review.records import FileReviewAction, ReviewSource
from ..data.file_review.action_scope import (
    finish_review_scoped_line_action,
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
    resolve_live_line_action_scope,
    resolve_live_to_batch_action_scope,
)
from ..data.session import require_session_started
from ..data.undo import undo_checkpoint
from ..core.buffer import (
    LineBuffer,
)
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.git_repository import require_git_repository
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
)
from .selection import discard_file_selection as _discard_file_selection
from .selection import discard_line_batching as _discard_line_batching
from .selection import discard_line_selection as _discard_line_selection
from .selection import selected_change_batch_discarding as _selected_change_batch_discarding
from .selection import selected_change_discarding as _selected_change_discarding
from .selection import selected_file_discarding as _selected_file_discarding
from .file_scope import discard_file as _file_scope_discard_file
from .file_scope import discard_file_replacement as _file_scope_discard_file_replacement
from .file_scope.discard_file_to_batch import discard_file_to_batch
from .selection.whole_file_batch_discarding import (
    discard_binary_to_batch,
    discard_text_deletion_to_batch,
)
from .selection.selected_hunk_refresh import (
    recalculate_selected_hunk_for_command,
    refresh_selected_hunk_after_line_action,
)
from .selection.action_completion import finish_selected_change_action


def command_discard(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Discard the selected hunk or binary file from the working tree."""

    log_journal("command_discard_start", quiet=quiet)

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if refuse_live_action_for_batch_selection(FileReviewAction.DISCARD):
        return
    if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.DISCARD):
        return
    refuse_bare_action_after_file_list("discard")
    refuse_bare_action_after_auto_advance_disabled("discard")

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        _selected_file_discarding.discard_selected_file(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    _selected_change_discarding.discard_selected_change(
        quiet=quiet,
        auto_advance=auto_advance,
    )


def command_discard_file(
    file: str,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Discard the entire specified file from the working tree.

    Args:
        file: File path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If explicit path, uses that file.
    """

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.DISCARD):
            return
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.DISCARD):
            return
        refuse_bare_action_after_file_list("discard --file")
        refuse_bare_action_after_auto_advance_disabled("discard --file")

    _file_scope_discard_file.discard_file_changes(
        file,
        auto_advance=auto_advance,
    )


def command_discard_file_as(
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Replace one live file-scoped working-tree file with explicit text."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    if file is None or file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.DISCARD):
            return
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.DISCARD):
            return
        refuse_bare_action_after_file_list("discard --file --as")
        refuse_bare_action_after_auto_advance_disabled("discard --file --as")

    _file_scope_discard_file_replacement.discard_file_as_replacement(
        replacement_text,
        file,
        auto_advance=auto_advance,
    )


def command_discard_line(
    line_id_specification: str,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Discard only the specified lines from the working tree.

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
        FileReviewAction.DISCARD,
        action_command=f"discard --line {line_id_specification}",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state
    operation_parts = ["discard", "--line", line_id_specification]
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        file_path = _discard_line_selection.discard_worktree_line_selection(
            line_id_specification,
            file=file,
        )
        print(
            _("✓ Discarded line(s): {lines} from {file}").format(
                lines=line_id_specification,
                file=file_path,
            ),
            file=sys.stderr,
        )
        refresh_selected_hunk_after_line_action(
            file_path,
            auto_advance=auto_advance,
        )
        finish_review_scoped_line_action(review_state)


def command_discard_to_batch(
    batch_name: str,
    line_ids: str | None = None,
    file: str | None = None,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Save to batch then discard from working tree.

    Args:
        batch_name: Name of batch to save to
        line_ids: Optional line IDs to discard
        file: Optional file path for file-scoped operations.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
        quiet: Suppress output
        advance: When quiet, advance the selection after discarding this file.
        auto_advance: Whether to select the next hunk after this action.

    Returns:
        Number of hunks saved to the batch and discarded.
    """
    require_git_repository()
    ensure_state_directory_exists()
    original_file_scope = file
    scope_resolution = resolve_live_to_batch_action_scope(
        FileReviewAction.DISCARD_TO_BATCH,
        command_name="discard",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
    )
    if scope_resolution.should_stop:
        return 0
    file = scope_resolution.file
    review_state = scope_resolution.review_state
    operation_parts = ["discard", "--to", batch_name]
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
                        "Cannot discard rename '{old} -> {new}' to a batch yet. "
                        "Discard, skip, or stage the rename first."
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
            exit_with_error(_("Discarding submodule pointer changes to a batch is not supported yet."))
        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.BINARY
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, BinaryFileChange):
                saved_hunks = discard_binary_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                saved_hunks = _selected_change_batch_discarding.discard_selected_change_to_batch(
                    batch_name,
                    file_only=False,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
        elif (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.DELETION
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, TextFileDeletionChange):
                saved_hunks = discard_text_deletion_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                saved_hunks = _selected_change_batch_discarding.discard_selected_change_to_batch(
                    batch_name,
                    file_only=False,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
        elif file is not None:
            # File-scoped operation

            # Determine target file
            if file == "":
                # --file with no arg: use selected hunk's file
                target_file = get_selected_change_file_path()
                if target_file is None:
                    exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
            else:
                target_file = file

            if line_ids is None:
                # --file without --line: discard entire file
                saved_hunks = discard_file_to_batch(
                    batch_name,
                    target_file,
                    quiet=quiet,
                    advance=advance,
                    auto_advance=auto_advance,
                )
            else:
                # --file with --line: discard specific lines from file
                saved_hunks = _discard_line_batching.discard_file_lines_to_batch(
                    batch_name,
                    target_file,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
        else:
            # Hunk-scoped operation (selected behavior)
            if line_ids is not None:
                saved_hunks = _discard_line_batching.discard_selected_lines_to_batch(
                    batch_name,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                # Discard entire selected hunk
                saved_hunks = _selected_change_batch_discarding.discard_selected_change_to_batch(
                    batch_name,
                    file_only=False,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
    if original_file_scope in (None, "") and line_ids is not None:
        finish_review_scoped_line_action(review_state)
    return saved_hunks


def command_discard_line_as_to_batch(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    no_edge_overlap: bool = False,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save replacement text to batch, then discard the original selection locally."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.DISCARD_TO_BATCH,
        action_command=f"discard --to {batch_name} --line {line_id_specification} --as",
        line_id_specification=line_id_specification,
        file=file,
        source=ReviewSource.FILE_VS_HEAD,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state

    replacement_payload = coerce_replacement_payload(replacement_text)
    operation_parts = [
        "discard",
        "--to", batch_name,
        "--line", line_id_specification,
        "--as", replacement_payload.display_text or "<stdin>",
    ]
    if no_edge_overlap:
        operation_parts.append("--no-edge-overlap")
    if file is not None:
        operation_parts.extend(["--file", file])
    with (
        undo_checkpoint(" ".join(operation_parts)),
        snapshot_selected_change_state() as saved_selected_state,
    ):
        preserve_selected_state = file not in (None, "")

        try:
            if file is None:
                require_selected_hunk()
            else:
                if file == "":
                    target_file = get_selected_change_file_path()
                    if target_file is None:
                        exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
                else:
                    target_file = file

                _discard_file_selection.load_explicit_file_selection(target_file)

            _discard_line_batching.discard_lines_as_to_batch(
                batch_name,
                line_id_specification,
                replacement_text,
                no_edge_overlap=no_edge_overlap,
                quiet=quiet,
                auto_advance=auto_advance,
            )

            if preserve_selected_state:
                restore_selected_change_state(saved_selected_state)
        except Exception:
            restore_selected_change_state(saved_selected_state)
            raise
    if file is None:
        finish_review_scoped_line_action(review_state)
    else:
        finish_review_scoped_line_action(review_state, file_path=target_file)
