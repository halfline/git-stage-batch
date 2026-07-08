"""Discard command implementation."""

from __future__ import annotations

from pathlib import Path
import sys
from contextlib import ExitStack

from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
)
from ..core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
    patch_is_new_file,
)
from ..core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ..core.models import BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ..data.hunk_tracking import (
    fetch_next_change,
)
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
from ..data.file_change_display import (
    render_gitlink_change,
    render_rename_change,
    render_text_deletion_change,
)
from ..data.file_hunk_display import cache_unstaged_file_as_single_hunk
from ..data.file_review.records import FileReviewAction, ReviewSource
from ..data.file_review.state import (
    clear_last_file_review_state_if_file_matches,
    finish_review_scoped_line_action,
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
    resolve_live_line_action_scope,
    resolve_live_to_batch_action_scope,
)
from ..data.file_tracking import auto_add_untracked_files
from ..data.live_diff import stream_live_git_diff
from ..data.progress import record_hunk_discarded
from ..data.session import require_session_started, snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..core.buffer import (
    LineBuffer,
    write_buffer_to_path,
)
from ..exceptions import CommandError, exit_with_error, NoMoreHunks
from ..i18n import _
from ..utils.file_io import (
    append_lines_to_file,
    path_is_empty,
    read_text_file_line_set,
    read_text_file_contents,
)
from ..utils.git import (
    get_git_repository_root_path,
    git_apply_to_worktree,
    git_checkout_paths,
    git_remove_paths,
    require_git_repository,
    run_git_command,
)
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)
from .selection import discard_file_selection as _discard_file_selection
from .selection import discard_line_batching as _discard_line_batching
from .selection import discard_line_selection as _discard_line_selection
from .selection import selected_change_batch_discarding as _selected_change_batch_discarding
from .file_scope.discard_file_to_batch import discard_file_to_batch
from .selection.whole_file_batch_discarding import (
    discard_binary_to_batch,
    discard_text_deletion_to_batch,
)
from .selection.selected_hunk_refresh import (
    recalculate_selected_hunk_for_command,
    refresh_selected_hunk_after_line_action,
)
from .selection.selected_change_discarding import (
    discard_gitlink_change,
    discard_rename_change,
    discard_text_deletion_change,
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
        _command_discard_selected_file(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    try:
        item = load_selected_change()
    except CommandError as error:
        if error.message == _("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."):
            item = None
        else:
            raise

    if item is None:
        try:
            item = fetch_next_change()
        except NoMoreHunks:
            if not quiet:
                print(_("No more hunks to process."), file=sys.stderr)
            return
    with undo_checkpoint("discard"):
        # Read cached hash
        patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()

        # Handle based on item type
        if isinstance(item, RenameChange):
            discard_rename_change(item)
            append_lines_to_file(get_block_list_file_path(), [patch_hash])
            record_hunk_discarded(patch_hash)

            if not quiet:
                print(
                    _("✓ Rename discarded: {old} -> {new}").format(
                        old=item.old_path,
                        new=item.new_path,
                    ),
                    file=sys.stderr,
                )

            finish_selected_change_action(
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return

        if isinstance(item, TextFileDeletionChange):
            discard_text_deletion_change(item)
            append_lines_to_file(get_block_list_file_path(), [patch_hash])
            record_hunk_discarded(patch_hash)

            if not quiet:
                print(_("✓ Text file deletion discarded: {file}").format(file=item.path()), file=sys.stderr)

            finish_selected_change_action(
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return

        if isinstance(item, GitlinkChange):
            discard_gitlink_change(item)
            append_lines_to_file(get_block_list_file_path(), [patch_hash])
            record_hunk_discarded(patch_hash)

            if not quiet:
                print(
                    _("✓ Submodule pointer restored: {file}").format(file=item.path()),
                    file=sys.stderr,
                )

            finish_selected_change_action(
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return

        if isinstance(item, BinaryFileChange):
            # Binary file - restore from HEAD or delete
            file_path = item.new_path if item.new_path != "/dev/null" else item.old_path

            # Snapshot file if untracked before discarding
            if file_path != "/dev/null":
                snapshot_file_if_untracked(file_path)

            log_journal("command_discard_binary_file", file_path=file_path, change_type=item.change_type)

            if item.is_new_file():
                # New file: delete from working tree
                absolute_path = get_git_repository_root_path() / file_path
                if absolute_path.exists():
                    absolute_path.unlink()
                    log_journal("command_discard_binary_deleted", file_path=file_path)
            elif item.is_deleted_file():
                # Deleted file: restore from HEAD
                result = git_checkout_paths("HEAD", [file_path], check=False)
                if result.returncode != 0:
                    print(_("Failed to restore binary file: {}").format(result.stderr), file=sys.stderr)
                    return
                log_journal("command_discard_binary_restored", file_path=file_path)
            else:
                # Modified file: restore from HEAD
                result = git_checkout_paths("HEAD", [file_path], check=False)
                if result.returncode != 0:
                    print(_("Failed to restore binary file: {}").format(result.stderr), file=sys.stderr)
                    return
                log_journal("command_discard_binary_restored", file_path=file_path)

            # Add hash to blocklist
            blocklist_path = get_block_list_file_path()
            append_lines_to_file(blocklist_path, [patch_hash])

            # Record for progress tracking
            record_hunk_discarded(patch_hash)

            if not quiet:
                change_desc = "added" if item.is_new_file() else ("deleted" if item.is_deleted_file() else "modified")
                print(_("✓ Binary file {desc} discarded: {file}").format(desc=change_desc, file=file_path), file=sys.stderr)

            finish_selected_change_action(
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return

        # Text hunk - use git apply -R
        with LineBuffer.from_path(get_selected_hunk_patch_file_path()) as patch_buffer:
            # Extract filename for user feedback and snapshotting
            line_changes = build_line_changes_from_patch_lines(patch_buffer)
            filename = line_changes.path
            is_new_file = patch_is_new_file(patch_buffer)

            # Snapshot file if untracked before discarding
            if filename != "/dev/null":
                snapshot_file_if_untracked(filename)

            log_journal("command_discard_before_git_apply", filename=filename, patch_hash=patch_hash)
            apply_result = git_apply_to_worktree(
                patch_buffer.byte_chunks(),
                reverse=True,
                check=False,
            )

        exit_code = apply_result.returncode
        stderr_text = apply_result.stderr or ""
        log_journal("command_discard_after_git_apply", exit_code=exit_code, stderr_len=len(stderr_text), filename=filename)

        if exit_code != 0:
            log_journal("command_discard_git_apply_failed", exit_code=exit_code, stderr=stderr_text, filename=filename)
            print(_("Failed to discard hunk: {}").format(stderr_text), file=sys.stderr)
            return

        # After reverse-applying a new file, delete it if it became empty
        # (git apply -R on new files empties them but doesn't delete them)
        if is_new_file:
            absolute_path = get_git_repository_root_path() / filename
            if absolute_path.exists():
                if path_is_empty(absolute_path):
                    absolute_path.unlink()

        # Add hash to blocklist
        blocklist_path = get_block_list_file_path()
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record for progress tracking
        record_hunk_discarded(patch_hash)

        log_journal("command_discard_success", filename=filename, patch_hash=patch_hash)

        if not quiet:
            print(_("✓ Hunk discarded from {file}").format(file=filename), file=sys.stderr)

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )


def _command_discard_selected_file(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Discard all changes from the currently selected file-scoped view."""
    target_file = get_selected_change_file_path()
    if target_file is None:
        if not quiet:
            print(_("No selected hunk. Run 'show' first or specify file path."), file=sys.stderr)
        return

    with undo_checkpoint("discard"):
        snapshot_file_if_untracked(target_file)

        head_result = run_git_command(
            ["show", f"HEAD:{target_file}"],
            check=False,
            text_output=False,
            requires_index_lock=False,
        )
        if head_result.returncode == 0:
            result = git_checkout_paths("HEAD", [target_file], check=False)
            if result.returncode != 0:
                if not quiet:
                    print(_("Failed to discard file: {}").format(result.stderr), file=sys.stderr)
                return
        else:
            absolute_path = get_git_repository_root_path() / target_file
            if absolute_path.exists():
                absolute_path.unlink()

        if quiet:
            finish_selected_change_action(quiet=True, auto_advance=auto_advance)
        else:
            print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)


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

    # Determine target file
    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            diff_result = run_git_command(
                [
                    "-c",
                    "diff.ignoreSubmodules=none",
                    "diff",
                    "--ignore-submodules=none",
                    "--quiet",
                ],
                check=False,
                requires_index_lock=False,
            )
            if diff_result.returncode == 0:
                print(_("No more hunks to process."), file=sys.stderr)
            else:
                print(_("No selected hunk. Run 'show' first or specify file path."), file=sys.stderr)
            return
    else:
        # Explicit path provided
        target_file = file
    auto_add_untracked_files([target_file])
    with undo_checkpoint(f"discard --file {file}".rstrip()):
        # Load blocklist
        blocklist_path = get_block_list_file_path()
        blocked_hashes = read_text_file_line_set(blocklist_path)

        deletion_change = render_text_deletion_change(target_file)
        if deletion_change is not None:
            patch_hash = compute_text_file_deletion_hash(deletion_change)
            discard_text_deletion_change(deletion_change)
            if patch_hash not in blocked_hashes:
                append_lines_to_file(blocklist_path, [patch_hash])
                record_hunk_discarded(patch_hash)
            print(
                _("✓ Text file deletion discarded: {file}").format(file=target_file),
                file=sys.stderr,
            )
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
            return

        gitlink_change = render_gitlink_change(target_file)
        if gitlink_change is not None:
            patch_hash = compute_gitlink_change_hash(gitlink_change)
            discard_gitlink_change(gitlink_change)
            if patch_hash not in blocked_hashes:
                append_lines_to_file(blocklist_path, [patch_hash])
                record_hunk_discarded(patch_hash)
            print(
                _("✓ Submodule pointer restored: {file}").format(file=target_file),
                file=sys.stderr,
            )
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
            return

        rename_change = render_rename_change(target_file)
        if rename_change is not None:
            patch_hash = compute_rename_change_hash(rename_change)
            discard_rename_change(rename_change)
            if patch_hash not in blocked_hashes:
                append_lines_to_file(blocklist_path, [patch_hash])
                record_hunk_discarded(patch_hash)
            print(
                _("✓ Rename discarded: {old} -> {new}").format(
                    old=rename_change.old_path,
                    new=rename_change.new_path,
                ),
                file=sys.stderr,
            )
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
            return

        # Snapshot the file if it's untracked (for abort functionality)
        snapshot_file_if_untracked(target_file)

        # Stream through hunks and collect hashes from target file BEFORE removing it
        # (git rm -f will stage the deletion, making hunks disappear from git diff)
        hashes_to_block = []
        with acquire_unified_diff(
            stream_live_git_diff(context_lines=get_context_lines())
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    if target_file not in (patch.old_path, patch.new_path):
                        continue

                    patch_hash = compute_rename_change_hash(patch)
                    if patch_hash not in blocked_hashes:
                        hashes_to_block.append(patch_hash)
                    continue

                if isinstance(patch, TextFileDeletionChange):
                    if patch.path() != target_file:
                        continue

                    patch_hash = compute_text_file_deletion_hash(patch)
                    if patch_hash not in blocked_hashes:
                        hashes_to_block.append(patch_hash)
                    continue

                if isinstance(patch, BinaryFileChange):
                    file_path = patch.new_path if patch.new_path != "/dev/null" else patch.old_path
                    if file_path != target_file:
                        continue

                    patch_hash = compute_binary_file_hash(patch)
                    if patch_hash not in blocked_hashes:
                        hashes_to_block.append(patch_hash)
                    continue

                if patch.new_path != target_file:
                    continue

                patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)

                if patch_hash not in blocked_hashes:
                    hashes_to_block.append(patch_hash)

        # Remove the file from working tree
        result = git_remove_paths([target_file], force=True, check=False)
        if result.returncode != 0:
            print(_("Failed to discard file: {}").format(result.stderr), file=sys.stderr)
            return

        # Mark all collected hashes as processed
        for patch_hash in hashes_to_block:
            append_lines_to_file(blocklist_path, [patch_hash])
            # Record for progress tracking
            record_hunk_discarded(patch_hash)

        print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)

        finish_selected_change_action(quiet=False, auto_advance=auto_advance)


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

    replacement_payload = coerce_replacement_payload(replacement_text)
    operation_parts = ["discard", "--as", replacement_payload.display_text or "<stdin>"]
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)), ExitStack() as selected_state_stack:
        preserve_selected_state = False
        saved_selected_state = None

        if file is None or file == "":
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file
            preserve_selected_state = True
            saved_selected_state = selected_state_stack.enter_context(
                snapshot_selected_change_state()
            )

        line_changes = _discard_file_selection.load_explicit_file_selection(target_file)
        snapshot_file_if_untracked(target_file)

        absolute_path = get_git_repository_root_path() / target_file
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(replacement_payload.data)

        if preserve_selected_state:
            assert saved_selected_state is not None
            restore_selected_change_state(saved_selected_state)
        else:
            recalculate_selected_hunk_for_command(
                line_changes.path,
                auto_advance=auto_advance,
            )
        clear_last_file_review_state_if_file_matches(target_file)

    print(_("✓ Discarded file as replacement: {file}").format(file=target_file), file=sys.stderr)


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
