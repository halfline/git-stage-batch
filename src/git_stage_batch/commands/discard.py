"""Discard command implementation."""

from __future__ import annotations

from pathlib import Path
import stat
import sys
from dataclasses import dataclass

from ..batch import (
    BatchFileUpdate,
    add_binary_file_to_batch,
    add_file_to_batch,
    add_files_to_batch,
    create_batch,
)
from ..batch.display import annotate_with_batch_source, annotate_with_batch_source_content
from ..batch.ownership import (
    BatchOwnership,
    _advance_source_content_preserving_existing_presence_with_provenance,
    _remap_batch_ownership_with_source_line_map,
    merge_batch_ownership,
    translate_lines_to_batch_ownership,
)
from ..batch.query import read_batch_metadata
from ..batch.selection import require_line_selection_in_view
from ..batch.source_refresh import prepare_batch_ownership_update_for_selection
from ..batch.source_refresh import _refresh_selected_lines_against_source_content
from ..batch.validation import batch_exists
from ..core.diff_parser import (
    build_line_changes_from_patch_bytes,
    parse_unified_diff_streaming,
)
from ..core.hashing import compute_binary_file_hash, compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection
from ..core.models import BinaryFileChange
from ..core.text_lifecycle import TextFileChangeType, detect_empty_text_lifecycle_change
from ..data.hunk_tracking import (
    SelectedChangeKind,
    advance_to_and_show_next_change,
    advance_to_next_change,
    build_file_hunk_from_content,
    cache_unstaged_file_as_single_hunk,
    fetch_next_change,
    get_selected_change_file_path,
    load_selected_change,
    read_selected_change_kind,
    recalculate_selected_hunk_for_file,
    record_hunk_discarded,
    record_hunks_discarded,
    refuse_bare_action_after_file_list,
    render_binary_file_change,
    require_selected_hunk,
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from ..data.file_review_state import (
    FileReviewAction,
    clear_last_file_review_state_if_file_matches,
    finish_review_scoped_line_action,
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
    resolve_live_line_action_scope,
    resolve_live_to_batch_action_scope,
    ReviewSource,
)
from ..data.line_state import load_line_changes_from_state
from ..data.batch_sources import create_batch_source_commit, load_session_batch_sources, save_session_batch_sources
from ..data.session import require_session_started, snapshot_file_if_untracked, snapshot_files_if_untracked
from ..data.undo import undo_checkpoint
from ..editor import (
    EditorBuffer,
    write_buffer_to_path,
)
from ..exceptions import CommandError, exit_with_error, NoMoreHunks
from ..i18n import _, ngettext
from ..output import print_remaining_line_changes_header
from ..staging.operations import build_target_working_tree_content_bytes_with_discarded_lines
from ..staging.operations import build_target_working_tree_content_bytes_with_replaced_lines
from ..staging.operations import build_target_working_tree_buffer_from_lines
from ..utils.command import ExitEvent, OutputEvent, stream_command
from ..utils.file_io import append_lines_to_file, read_file_bytes, read_text_file_contents
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command, stream_git_command
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)
from .include import (
    _expand_replacement_selection_ids,
)


@dataclass(frozen=True)
class _PreparedPatchDiscard:
    patch_bytes: bytes
    patch_hash: str


@dataclass
class _TextFileDiscardInput:
    file_path: str
    file_mode: str
    all_lines_to_batch: list
    patches_to_discard: list[_PreparedPatchDiscard]


@dataclass(frozen=True)
class _CollectedTextFileDiscards:
    inputs_by_file: dict[str, _TextFileDiscardInput]
    files_with_text_patches: set[str]


@dataclass(frozen=True)
class _PreparedTextFileDiscardToBatch:
    file_path: str
    file_mode: str
    ownership: BatchOwnership
    batch_source_commit: str | None
    patches_to_discard: list[_PreparedPatchDiscard]


@dataclass(frozen=True)
class DiscardFilesToBatchResult:
    discarded_hunks: int
    discarded_files: list[str]


def _load_explicit_file_selection(file_path: str):
    """Return the active file-scoped view for an explicit discard target."""
    reuse_selected_file_view = (
        read_selected_change_kind() == SelectedChangeKind.FILE
        and get_selected_change_file_path() == file_path
    )
    if reuse_selected_file_view:
        line_changes = load_line_changes_from_state()
    else:
        line_changes = cache_unstaged_file_as_single_hunk(file_path)

    if line_changes is None:
        exit_with_error(_("No changes in file '{file}'.").format(file=file_path))
    return line_changes


def command_discard(*, quiet: bool = False) -> None:
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

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        _command_discard_selected_file(quiet=quiet)
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
                result = run_git_command(["checkout", "HEAD", "--", file_path], check=False)
                if result.returncode != 0:
                    print(_("Failed to restore binary file: {}").format(result.stderr), file=sys.stderr)
                    return
                log_journal("command_discard_binary_restored", file_path=file_path)
            else:
                # Modified file: restore from HEAD
                result = run_git_command(["checkout", "HEAD", "--", file_path], check=False)
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

            if quiet:
                advance_to_next_change()
            else:
                advance_to_and_show_next_change()
            return

        # Text hunk - use git apply -R
        patch_bytes = read_file_bytes(get_selected_hunk_patch_file_path())

        # Extract filename for user feedback and snapshotting
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)
        filename = line_changes.path

        # Snapshot file if untracked before discarding
        if filename != "/dev/null":
            snapshot_file_if_untracked(filename)

        # Apply the hunk in reverse to discard from working tree using streaming
        log_journal("command_discard_before_git_apply", filename=filename, patch_hash=patch_hash)
        stderr_chunks = []
        exit_code = 0

        for event in stream_command(["git", "apply", "--reverse"], [patch_bytes]):
            if isinstance(event, ExitEvent):
                exit_code = event.exit_code
            elif isinstance(event, OutputEvent):
                if event.fd == 2:  # stderr
                    stderr_chunks.append(event.data)

        stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace') if stderr_chunks else ""
        log_journal("command_discard_after_git_apply", exit_code=exit_code, stderr_len=len(stderr_text), filename=filename)

        if exit_code != 0:
            log_journal("command_discard_git_apply_failed", exit_code=exit_code, stderr=stderr_text, filename=filename)
            print(_("Failed to discard hunk: {}").format(stderr_text), file=sys.stderr)
            return

        # After reverse-applying a new file, delete it if it became empty
        # (git apply -R on new files empties them but doesn't delete them)
        is_new_file = b"--- /dev/null" in patch_bytes
        if is_new_file:
            absolute_path = get_git_repository_root_path() / filename
            if absolute_path.exists():
                content = read_text_file_contents(absolute_path)
                if not content.strip():  # File is empty
                    absolute_path.unlink()

        # Add hash to blocklist
        blocklist_path = get_block_list_file_path()
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record for progress tracking
        record_hunk_discarded(patch_hash)

        log_journal("command_discard_success", filename=filename, patch_hash=patch_hash)

        if not quiet:
            print(_("✓ Hunk discarded from {file}").format(file=filename), file=sys.stderr)

        if quiet:
            advance_to_next_change()
        else:
            advance_to_and_show_next_change()


def _command_discard_selected_file(*, quiet: bool = False) -> None:
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
        )
        if head_result.returncode == 0:
            result = run_git_command(["checkout", "HEAD", "--", target_file], check=False)
            if result.returncode != 0:
                if not quiet:
                    print(_("Failed to discard file: {}").format(result.stderr), file=sys.stderr)
                return
        else:
            absolute_path = get_git_repository_root_path() / target_file
            if absolute_path.exists():
                absolute_path.unlink()

        if quiet:
            advance_to_next_change()
        else:
            print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)
            advance_to_and_show_next_change()


def command_discard_file(file: str) -> None:
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

    # Determine target file
    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            diff_result = run_git_command(["diff", "--quiet"], check=False)
            if diff_result.returncode == 0:
                print(_("No more hunks to process."), file=sys.stderr)
            else:
                print(_("No selected hunk. Run 'show' first or specify file path."), file=sys.stderr)
            return
    else:
        # Explicit path provided
        target_file = file
    with undo_checkpoint(f"discard --file {file}".rstrip()):
        # Load blocklist
        blocklist_path = get_block_list_file_path()
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())

        # Snapshot the file if it's untracked (for abort functionality)
        snapshot_file_if_untracked(target_file)

        # Stream through hunks and collect hashes from target file BEFORE removing it
        # (git rm -f will stage the deletion, making hunks disappear from git diff)
        hashes_to_block = []
        for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
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

            patch_bytes = patch.to_patch_bytes()
            patch_hash = compute_stable_hunk_hash(patch_bytes)

            if patch_hash not in blocked_hashes:
                hashes_to_block.append(patch_hash)

        # Remove the file from working tree
        result = run_git_command(["rm", "-f", target_file], check=False)
        if result.returncode != 0:
            print(_("Failed to discard file: {}").format(result.stderr), file=sys.stderr)
            return

        # Mark all collected hashes as processed
        for patch_hash in hashes_to_block:
            append_lines_to_file(blocklist_path, [patch_hash])
            # Record for progress tracking
            record_hunk_discarded(patch_hash)

        print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)

        advance_to_and_show_next_change()


def command_discard_file_as(replacement_text: str, file: str | None = None) -> None:
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

    operation_parts = ["discard", "--as", replacement_text]
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)):
        preserve_selected_state = False
        saved_selected_state = None

        if file is None or file == "":
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file
            preserve_selected_state = True
            saved_selected_state = snapshot_selected_change_state()

        line_changes = _load_explicit_file_selection(target_file)
        snapshot_file_if_untracked(target_file)

        absolute_path = get_git_repository_root_path() / target_file
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(replacement_text, encoding="utf-8", errors="surrogateescape")

        if preserve_selected_state:
            restore_selected_change_state(saved_selected_state)
        else:
            recalculate_selected_hunk_for_file(line_changes.path)
        clear_last_file_review_state_if_file_matches(target_file)

    print(_("✓ Discarded file as replacement: {file}").format(file=target_file), file=sys.stderr)


def command_discard_line(line_id_specification: str, file: str | None = None) -> None:
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
        if file is None:
            require_selected_hunk()
            line_changes = load_line_changes_from_state()
        else:
            if file == "":
                target_file = get_selected_change_file_path()
                if target_file is None:
                    exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
            else:
                target_file = file

            line_changes = _load_explicit_file_selection(target_file)

        requested_ids = parse_line_selection(line_id_specification)
        require_line_selection_in_view(
            line_changes,
            set(requested_ids),
            line_id_specification=line_id_specification,
        )

        working_file_path = get_git_repository_root_path() / line_changes.path
        if not working_file_path.exists():
            exit_with_error(_("File not found in working tree: {file}").format(file=line_changes.path))

        with EditorBuffer.from_path(working_file_path) as working_lines:
            target_working_buffer = build_target_working_tree_buffer_from_lines(
                line_changes,
                set(requested_ids),
                working_lines,
                working_has_trailing_newline=_buffer_ends_with_lf(working_lines),
            )

        # Write back to working tree
        with target_working_buffer:
            write_buffer_to_path(working_file_path, target_working_buffer)

        # After modifying working tree, recalculate hunk for the SAME file
        print(
            _("✓ Discarded line(s): {lines} from {file}").format(
                lines=line_id_specification,
                file=line_changes.path,
            ),
            file=sys.stderr,
        )
        print_remaining_line_changes_header(line_changes.path)
        recalculate_selected_hunk_for_file(line_changes.path)
        finish_review_scoped_line_action(review_state)


def command_discard_to_batch(
    batch_name: str,
    line_ids: str | None = None,
    file: str | None = None,
    *,
    quiet: bool = False,
    advance: bool = True,
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
            and read_selected_change_kind() == SelectedChangeKind.BINARY
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, BinaryFileChange):
                saved_hunks = _command_discard_binary_to_batch(batch_name, selected_change, quiet=quiet)
            else:
                saved_hunks = _command_discard_hunk_to_batch(batch_name, file_only=False, quiet=quiet)
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
                saved_hunks = _command_discard_file_to_batch(batch_name, target_file, quiet=quiet, advance=advance)
            else:
                # --file with --line: discard specific lines from file
                saved_hunks = _command_discard_file_lines_to_batch(batch_name, target_file, line_ids, quiet=quiet)
        else:
            # Hunk-scoped operation (selected behavior)
            if line_ids is not None:
                saved_hunks = _command_discard_lines_to_batch(batch_name, line_ids, quiet=quiet)
            else:
                # Discard entire selected hunk
                saved_hunks = _command_discard_hunk_to_batch(batch_name, file_only=False, quiet=quiet)
    if original_file_scope in (None, "") and line_ids is not None:
        finish_review_scoped_line_action(review_state)
    return saved_hunks


def command_discard_line_as_to_batch(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str,
    file: str | None = None,
    *,
    no_edge_overlap: bool = False,
    quiet: bool = False,
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

    operation_parts = [
        "discard",
        "--to", batch_name,
        "--line", line_id_specification,
        "--as", replacement_text,
    ]
    if no_edge_overlap:
        operation_parts.append("--no-edge-overlap")
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        saved_selected_state = snapshot_selected_change_state()
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

                _load_explicit_file_selection(target_file)

            _command_discard_lines_to_batch_as(
                batch_name,
                line_id_specification,
                replacement_text,
                no_edge_overlap=no_edge_overlap,
                quiet=quiet,
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


def _command_discard_lines_to_batch_as(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str,
    *,
    no_edge_overlap: bool = False,
    quiet: bool = False,
) -> None:
    """Persist replacement text to batch and discard original selected lines."""
    line_changes = load_line_changes_from_state()
    requested_ids = set(parse_line_selection(line_id_specification))
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    effective_ids = _expand_replacement_selection_ids(line_changes, requested_ids)

    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    selected_lines = [line for line in line_changes.lines if line.id in effective_ids]
    if not selected_lines:
        exit_with_error(_("No matching lines found for selection: {ids}").format(ids=line_id_specification))

    working_file_path = get_git_repository_root_path() / line_changes.path
    if not working_file_path.exists():
        exit_with_error(_("File not found in working tree: {file}").format(file=line_changes.path))
    working_bytes = working_file_path.read_bytes()

    try:
        rewritten_working_content = build_target_working_tree_content_bytes_with_replaced_lines(
            line_changes,
            effective_ids,
            replacement_text,
            working_bytes,
            trim_unchanged_edge_anchors=not no_edge_overlap,
        )
    except ValueError as e:
        exit_with_error(str(e))

    try:
        rewritten_cached_lines = build_file_hunk_from_content(line_changes.path, rewritten_working_content)
        if rewritten_cached_lines is None:
            exit_with_error(_("No changes in file '{file}'.").format(file=line_changes.path))
        rewritten_line_changes = annotate_with_batch_source_content(
            line_changes.path,
            rewritten_cached_lines,
            rewritten_working_content.decode("utf-8", errors="replace"),
        )
        rewritten_selected_lines = _select_rewritten_replacement_lines(
            selected_lines,
            rewritten_line_changes,
        )

        metadata = read_batch_metadata(batch_name)
        existing_ownership = None
        current_batch_source = None
        batch_source_commit = None
        if line_changes.path in metadata.get("files", {}):
            file_metadata = metadata["files"][line_changes.path]
            existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
            current_batch_source = file_metadata.get("batch_source_commit")

        try:
            if existing_ownership is None:
                update = prepare_batch_ownership_update_for_selection(
                    batch_name=batch_name,
                    file_path=line_changes.path,
                    current_batch_source_commit=current_batch_source,
                    existing_ownership=existing_ownership,
                    selected_lines=rewritten_selected_lines,
                )
                ownership = update.ownership_after
                batch_source_commit = update.batch_source_commit
            else:
                old_source_result = run_git_command(
                    ["show", f"{current_batch_source}:{line_changes.path}"],
                    text_output=False,
                    check=False,
                )
                if old_source_result.returncode != 0:
                    exit_with_error(
                        _("Cannot discard lines to batch: failed to read batch source for '{file}'.").format(
                            file=line_changes.path
                        )
                    )

                advanced_source = _advance_source_content_preserving_existing_presence_with_provenance(
                    old_source_content=old_source_result.stdout,
                    working_content=rewritten_working_content,
                    ownership=existing_ownership,
                )
                remapped_existing_ownership = _remap_batch_ownership_with_source_line_map(
                    ownership=existing_ownership,
                    source_line_map=advanced_source.source_line_map,
                )
                refreshed_selected_lines = _refresh_selected_lines_against_source_content(
                    rewritten_selected_lines,
                    source_content=advanced_source.content,
                    working_content=rewritten_working_content,
                    working_line_map=advanced_source.working_line_map,
                )
                new_ownership = translate_lines_to_batch_ownership(refreshed_selected_lines)
                ownership = merge_batch_ownership(remapped_existing_ownership, new_ownership)
                batch_source_commit = create_batch_source_commit(
                    line_changes.path,
                    file_content_override=advanced_source.content,
                )
                batch_sources = load_session_batch_sources()
                batch_sources[line_changes.path] = batch_source_commit
                save_session_batch_sources(batch_sources)
        except ValueError as e:
            exit_with_error(
                _("Cannot discard lines to batch: batch source is stale and remapping failed.\n"
                  "File: {file}\n"
                  "Batch: {batch}\n"
                  "Error: {error}").format(file=line_changes.path, batch=batch_name, error=str(e))
            )

        file_mode = _detect_file_mode(line_changes.path)

        if batch_source_commit is None:
            batch_source_commit = create_batch_source_commit(
                line_changes.path,
                file_content_override=rewritten_working_content,
            )
            batch_sources = load_session_batch_sources()
            batch_sources[line_changes.path] = batch_source_commit
            save_session_batch_sources(batch_sources)

        snapshot_file_if_untracked(line_changes.path)
        add_file_to_batch(
            batch_name,
            line_changes.path,
            ownership,
            file_mode,
            batch_source_commit=batch_source_commit,
        )

        rewritten_selected_ids = {
            line.id for line in rewritten_selected_lines if line.id is not None
        }
        target_working_content = build_target_working_tree_content_bytes_with_discarded_lines(
            rewritten_line_changes,
            rewritten_selected_ids,
            rewritten_working_content,
        )

    except Exception:
        working_file_path.write_bytes(working_bytes)
        raise

    working_file_path.write_bytes(target_working_content)

    if not quiet:
        print(
            _("✓ Discarded line(s) as replacement to batch '{name}': {lines}").format(
                name=batch_name,
                lines=line_id_specification,
            ),
            file=sys.stderr,
        )

    recalculate_selected_hunk_for_file(line_changes.path)


def _select_rewritten_replacement_lines(
    original_selected_lines: list,
    rewritten_line_changes,
) -> list:
    """Find the rewritten changed span that overlaps the original selection."""
    original_old_lines = {
        line.old_line_number
        for line in original_selected_lines
        if line.old_line_number is not None
    }
    original_new_lines = {
        line.new_line_number
        for line in original_selected_lines
        if line.new_line_number is not None
    }

    matching_indices = [
        index
        for index, line in enumerate(rewritten_line_changes.lines)
        if line.kind != " " and (
            line.old_line_number in original_old_lines
            or line.new_line_number in original_new_lines
        )
    ]
    if matching_indices:
        start_index = min(matching_indices)
        end_index = max(matching_indices)
        return [
            line
            for line in rewritten_line_changes.lines[start_index:end_index + 1]
            if line.kind != " "
        ]

    exit_with_error(_("Replacement selection could not be located after rewriting the file."))


def _detect_file_mode(file_path: str) -> str:
    """Return the current git file mode for a path, defaulting to a regular file."""
    return _detect_file_mode_from_root(get_git_repository_root_path(), file_path)


def _detect_file_mode_from_root(repo_root: Path, file_path: str) -> str:
    """Return the current git file mode using a known repository root."""
    absolute_path = repo_root / file_path
    if absolute_path.exists():
        return "100755" if absolute_path.stat().st_mode & stat.S_IXUSR else "100644"

    ls_result = run_git_command(["ls-files", "-s", "--", file_path], check=False)
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            return parts[0]
    return "100644"


def _discard_binary_change_from_working_tree(binary_change: BinaryFileChange) -> None:
    """Discard one live binary change from the working tree."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    absolute_path = get_git_repository_root_path() / file_path

    if binary_change.is_new_file():
        if absolute_path.exists():
            absolute_path.unlink()
        run_git_command(["rm", "--cached", "--quiet", "--", file_path], check=False)
        return

    result = run_git_command(["checkout", "HEAD", "--", file_path], check=False)
    if result.returncode != 0:
        exit_with_error(_("Failed to restore binary file: {error}").format(error=result.stderr))


def _command_discard_binary_to_batch(
    batch_name: str,
    binary_change: BinaryFileChange,
    *,
    quiet: bool = False,
    advance: bool = True,
) -> int:
    """Save one binary change to a batch, then discard it from the working tree."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    patch_hash = compute_binary_file_hash(binary_change)

    snapshot_file_if_untracked(file_path)
    add_binary_file_to_batch(
        batch_name,
        binary_change,
        file_mode=_detect_file_mode(file_path),
    )
    _discard_binary_change_from_working_tree(binary_change)

    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_hunk_discarded(patch_hash)

    if not quiet:
        print(
            _("Discarded binary file '{file}' to batch '{batch}'").format(
                file=file_path,
                batch=batch_name,
            ),
            file=sys.stderr,
        )

    if quiet and advance:
        advance_to_next_change()
    elif not quiet:
        advance_to_and_show_next_change()
    return 1


def _prepare_text_file_discard_to_batch(
    batch_name: str,
    discard_input: _TextFileDiscardInput,
    *,
    metadata: dict,
) -> _PreparedTextFileDiscardToBatch | None:
    """Prepare one normal text file discard without publishing batch state."""
    if not discard_input.all_lines_to_batch:
        return None

    file_path = discard_input.file_path
    existing_ownership = None
    current_batch_source = None
    if file_path in metadata.get("files", {}):
        file_metadata = metadata["files"][file_path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=discard_input.all_lines_to_batch,
        )
    except ValueError as e:
        exit_with_error(
            _("Cannot discard file to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
        )

    return _PreparedTextFileDiscardToBatch(
        file_path=file_path,
        file_mode=discard_input.file_mode,
        ownership=update.ownership_after,
        batch_source_commit=update.batch_source_commit,
        patches_to_discard=discard_input.patches_to_discard,
    )


def _collect_text_file_discard_inputs(
    files: list[str],
    *,
    blocked_hashes: set[str],
) -> _CollectedTextFileDiscards:
    """Collect normal text file discard inputs from one Git diff."""
    if not files:
        return _CollectedTextFileDiscards(inputs_by_file={}, files_with_text_patches=set())

    repo_root = get_git_repository_root_path()
    inputs_by_file: dict[str, _TextFileDiscardInput] = {}
    files_with_text_patches: set[str] = set()

    for patch in parse_unified_diff_streaming(
        stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color", "HEAD", "--", *files])
    ):
        if isinstance(patch, BinaryFileChange):
            continue

        file_path = patch.new_path if patch.new_path != "/dev/null" else patch.old_path
        files_with_text_patches.add(file_path)

        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)
        if patch_hash in blocked_hashes:
            continue

        hunk_lines = build_line_changes_from_patch_bytes(
            patch_bytes,
            annotator=annotate_with_batch_source,
        )
        discard_input = inputs_by_file.get(file_path)
        if discard_input is None:
            discard_input = _TextFileDiscardInput(
                file_path=file_path,
                file_mode=_detect_file_mode_from_root(repo_root, file_path),
                all_lines_to_batch=[],
                patches_to_discard=[],
            )
            inputs_by_file[file_path] = discard_input
        discard_input.all_lines_to_batch.extend(hunk_lines.lines)
        discard_input.patches_to_discard.append(
            _PreparedPatchDiscard(
                patch_bytes=patch_bytes,
                patch_hash=patch_hash,
            )
        )
        blocked_hashes.add(patch_hash)

    return _CollectedTextFileDiscards(
        inputs_by_file=inputs_by_file,
        files_with_text_patches=files_with_text_patches,
    )


def _run_reverse_apply_for_prepared_discards(
    prepared_discards: list[_PreparedTextFileDiscardToBatch],
    *,
    check_only: bool = False,
) -> None:
    arguments = ["git", "apply", "--reverse", "--unidiff-zero"]
    if check_only:
        arguments.append("--check")

    exit_code = 0
    stderr_chunks = []
    patch_chunks = [
        patch.patch_bytes
        for prepared in prepared_discards
        for patch in prepared.patches_to_discard
    ]
    for event in stream_command(arguments, stdin_chunks=patch_chunks):
        if isinstance(event, ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, OutputEvent) and event.fd == 2:
            stderr_chunks.append(event.data)

    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        exit_with_error(_("Failed to discard changes from file: {err}").format(err=stderr_text))


def _discard_prepared_text_files_to_batch(
    batch_name: str,
    prepared_discards: list[_PreparedTextFileDiscardToBatch],
) -> DiscardFilesToBatchResult:
    """Publish prepared text file discards once, then update the worktree."""
    if not prepared_discards:
        return DiscardFilesToBatchResult(discarded_hunks=0, discarded_files=[])

    snapshot_files_if_untracked([prepared.file_path for prepared in prepared_discards])

    _run_reverse_apply_for_prepared_discards(prepared_discards, check_only=True)
    add_files_to_batch(
        batch_name,
        [
            BatchFileUpdate(
                file_path=prepared.file_path,
                ownership=prepared.ownership,
                file_mode=prepared.file_mode,
                batch_source_commit=prepared.batch_source_commit,
            )
            for prepared in prepared_discards
        ],
    )
    _run_reverse_apply_for_prepared_discards(prepared_discards)

    repo_root = get_git_repository_root_path()
    for prepared in prepared_discards:
        full_path = repo_root / prepared.file_path
        if not full_path.exists():
            run_git_command(["rm", "--cached", "--quiet", "--", prepared.file_path], check=False)

    hunk_hashes = [
        patch.patch_hash
        for prepared in prepared_discards
        for patch in prepared.patches_to_discard
    ]
    record_hunks_discarded(hunk_hashes)

    return DiscardFilesToBatchResult(
        discarded_hunks=len(hunk_hashes),
        discarded_files=[
            prepared.file_path
            for prepared in prepared_discards
            if prepared.patches_to_discard
        ],
    )


def command_discard_files_to_batch(
    batch_name: str,
    files: list[str],
    *,
    quiet: bool = False,
    advance: bool = True,
) -> DiscardFilesToBatchResult:
    """Save resolved text files to a batch with one batch publication."""
    require_git_repository()
    ensure_state_directory_exists()

    if not files:
        return DiscardFilesToBatchResult(discarded_hunks=0, discarded_files=[])
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())
    metadata = read_batch_metadata(batch_name)
    collected_discards = _collect_text_file_discard_inputs(
        files,
        blocked_hashes=blocked_hashes,
    )

    prepared_discards: list[_PreparedTextFileDiscardToBatch] = []
    total_hunks = 0
    discarded_files: list[str] = []

    def flush_prepared() -> None:
        nonlocal metadata, total_hunks
        nonlocal discarded_files, prepared_discards
        result = _discard_prepared_text_files_to_batch(batch_name, prepared_discards)
        if result.discarded_hunks:
            total_hunks += result.discarded_hunks
            discarded_files.extend(result.discarded_files)
            metadata = read_batch_metadata(batch_name)
        prepared_discards = []

    for file_path in files:
        log_journal("discard_file_to_batch_start", batch_name=batch_name, file_path=file_path, quiet=quiet)
        discard_input = collected_discards.inputs_by_file.get(file_path)
        if discard_input is None and file_path in collected_discards.files_with_text_patches:
            continue

        prepared = _prepare_text_file_discard_to_batch(
            batch_name,
            discard_input,
            metadata=metadata,
        ) if discard_input is not None else None
        if prepared is None:
            flush_prepared()
            discarded_hunks = command_discard_to_batch(
                batch_name,
                file=file_path,
                quiet=True,
                advance=False,
            )
            if discarded_hunks > 0:
                total_hunks += discarded_hunks
                discarded_files.append(file_path)
                metadata = read_batch_metadata(batch_name)
                blocklist_text = read_text_file_contents(blocklist_path)
                blocked_hashes = set(blocklist_text.splitlines())
            continue

        prepared_discards.append(prepared)
        log_journal("discard_file_to_batch_end", batch_name=batch_name, file_path=file_path)

    flush_prepared()

    if quiet and advance:
        advance_to_next_change()
    elif not quiet:
        advance_to_and_show_next_change()

    return DiscardFilesToBatchResult(
        discarded_hunks=total_hunks,
        discarded_files=discarded_files,
    )


def _command_discard_file_to_batch(
    batch_name: str,
    file_path: str,
    *,
    quiet: bool = False,
    advance: bool = True,
) -> int:
    """Discard entire file to batch (internal helper for file-scoped operations)."""

    log_journal("discard_file_to_batch_start", batch_name=batch_name, file_path=file_path, quiet=quiet)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    binary_change = render_binary_file_change(file_path)
    if binary_change is not None:
        return _command_discard_binary_to_batch(batch_name, binary_change, quiet=quiet, advance=advance)

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Detect file mode
    file_mode = _detect_file_mode(file_path)

    # Collect ALL hunks from this file (live working tree state)
    all_lines_to_batch = []
    patches_to_discard = []

    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color", "HEAD", "--", file_path])):
        patch_bytes_loop = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes_loop)

        if patch_hash in blocked_hashes:
            continue

        # Parse hunk to get lines
        hunk_lines = build_line_changes_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
        all_lines_to_batch.extend(hunk_lines.lines)
        patches_to_discard.append((patch_bytes_loop, patch_hash))

    if not all_lines_to_batch:
        # Special case: empty text lifecycle changes have no hunk body.
        repo_root = get_git_repository_root_path()
        full_path = repo_root / file_path
        lifecycle_change_type = detect_empty_text_lifecycle_change(file_path)
        if lifecycle_change_type is not None:
            snapshot_file_if_untracked(file_path)
            add_file_to_batch(
                batch_name,
                file_path,
                BatchOwnership([], []),
                file_mode,
                change_type=lifecycle_change_type,
            )

            if lifecycle_change_type == TextFileChangeType.ADDED:
                # Delete from working tree
                full_path.unlink()

                # Remove from index if present (from intent-to-add)
                run_git_command(["rm", "--cached", "--quiet", "--", file_path], check=False)
            else:
                run_git_command(["checkout", "HEAD", "--", file_path], check=False)

            if not quiet:
                print(_("Discarded file '{file}' to batch '{batch}'").format(file=file_path, batch=batch_name), file=sys.stderr)

            log_journal("discard_file_to_batch_end", batch_name=batch_name, file_path=file_path)
            return 1

        if not quiet:
            print(_("No changes in file '{file}' to discard.").format(file=file_path), file=sys.stderr)
        return 0

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if file_path in metadata.get("files", {}):
        file_metadata = metadata["files"][file_path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=all_lines_to_batch
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        exit_with_error(
            _("Cannot discard file to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
        )

    # Snapshot file before modifying
    snapshot_file_if_untracked(file_path)

    # Save to batch
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    # Record hunks as discarded for progress tracking
    for patch_bytes, patch_hash in patches_to_discard:
        record_hunk_discarded(patch_hash)

    # Discard from working tree (reverse patches)
    for patch_bytes_item, patch_hash in patches_to_discard:
        log_journal("discard_file_to_batch_before_git_apply", batch_name=batch_name, patch_hash=patch_hash, file_path=file_path, patch_content=patch_bytes_item.decode("utf-8", errors="replace"))

        exit_code = 0
        stderr_chunks = []

        for event in stream_command(
            ["git", "apply", "--reverse", "--unidiff-zero"],
            stdin_chunks=[patch_bytes_item]
        ):
            if isinstance(event, ExitEvent):
                exit_code = event.exit_code
            elif isinstance(event, OutputEvent) and event.fd == 2:  # stderr
                stderr_chunks.append(event.data)

        if exit_code != 0:
            stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace')
            exit_with_error(_("Failed to discard changes from file: {err}").format(err=stderr_text))

        log_journal("discard_file_to_batch_after_git_apply", batch_name=batch_name, patch_hash=patch_hash, exit_code=exit_code)

    # If file was deleted from working tree, remove from index too
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    if not full_path.exists():
        # File was deleted by git apply --reverse, remove from index
        run_git_command(["rm", "--cached", "--quiet", "--", file_path], check=False)

    if not quiet:
        print(_("Discarded file '{file}' to batch '{batch}'").format(file=file_path, batch=batch_name), file=sys.stderr)

    log_journal("discard_file_to_batch_end", batch_name=batch_name, file_path=file_path)

    # Show next hunk
    if quiet and advance:
        advance_to_next_change()
    elif not quiet:
        advance_to_and_show_next_change()
    return len(patches_to_discard)


def _command_discard_file_lines_to_batch(batch_name: str, file_path: str, line_id_specification: str, *, quiet: bool = False) -> int:
    """Discard specific lines from a file to batch (file-scoped with line IDs)."""

    cached_lines = _load_explicit_file_selection(file_path)

    # Annotate with batch source line numbers
    line_changes = annotate_with_batch_source(file_path, cached_lines)

    # Parse line IDs and filter to selected lines
    requested_ids = set(parse_line_selection(line_id_specification))
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    selected_lines = [line for line in line_changes.lines if line.id in requested_ids]

    if not selected_lines:
        if not quiet:
            print(_("No lines match the specified IDs in file '{file}'.").format(file=file_path), file=sys.stderr)
        return 0

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    file_mode = _detect_file_mode(file_path)

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if file_path in metadata.get("files", {}):
        file_metadata = metadata["files"][file_path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=selected_lines
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        exit_with_error(
            _("Cannot discard lines to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
        )

    # Snapshot file before modifying
    snapshot_file_if_untracked(file_path)

    # Save to batch
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    # Now discard selected lines from working tree
    working_file_path = get_git_repository_root_path() / file_path
    if working_file_path.exists():
        working_bytes = working_file_path.read_bytes()
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=file_path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_bytes_with_discarded_lines(
        line_changes, requested_ids, working_bytes)

    # Write back to working tree
    working_file_path.write_bytes(target_working_content)

    if not quiet:
        print(_("Discarded line(s) from file '{file}' to batch '{batch}': {lines}").format(
            file=file_path,
            batch=batch_name,
            lines=line_id_specification
        ), file=sys.stderr)

    # Show next hunk
    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()
    return 1


def _command_discard_lines_to_batch(batch_name: str, line_id_specification: str, *, quiet: bool = False) -> int:
    """Save specific lines to batch and discard them from working tree (internal helper)."""

    log_journal("discard_lines_to_batch_start", batch_name=batch_name, line_ids=line_id_specification, quiet=quiet)

    require_selected_hunk()

    requested_ids = set(parse_line_selection(line_id_specification))
    line_changes = load_line_changes_from_state()
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )

    # Filter to requested display line IDs
    selected_lines = [line for line in line_changes.lines if line.id in requested_ids]
    if not selected_lines:
        exit_with_error(_("No matching lines found for selection: {ids}").format(ids=line_id_specification))

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if line_changes.path in metadata.get("files", {}):
        file_metadata = metadata["files"][line_changes.path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=line_changes.path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=selected_lines
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        exit_with_error(
            _("Cannot discard lines to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=line_changes.path, batch=batch_name, error=str(e))
        )

    file_mode = _detect_file_mode(line_changes.path)

    # add_file_to_batch creates the batch source commit from this snapshot.
    snapshot_file_if_untracked(line_changes.path)

    log_journal("discard_lines_to_batch_before_add", batch_name=batch_name, file_path=line_changes.path)

    # Save to batch using batch source model
    add_file_to_batch(batch_name, line_changes.path, ownership, file_mode)

    log_journal("discard_lines_to_batch_after_add", batch_name=batch_name, file_path=line_changes.path)

    # Now discard those lines from working tree
    working_file_path = get_git_repository_root_path() / line_changes.path
    if working_file_path.exists():
        working_bytes = working_file_path.read_bytes()
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=line_changes.path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_bytes_with_discarded_lines(
        line_changes, requested_ids, working_bytes)

    # Write back to working tree
    log_journal("discard_lines_to_batch_before_write", file_path=str(working_file_path))
    working_file_path.write_bytes(target_working_content)
    log_journal("discard_lines_to_batch_after_write", file_path=str(working_file_path))

    if not quiet:
        print(_("✓ Discarded line(s) to batch '{name}': {lines}").format(name=batch_name, lines=line_id_specification), file=sys.stderr)

    # After modifying working tree, recalculate and show the updated hunk for this file
    recalculate_selected_hunk_for_file(line_changes.path)

    log_journal("discard_lines_to_batch_success", batch_name=batch_name, line_ids=line_id_specification, file_path=line_changes.path)
    return 1


def _command_discard_hunk_to_batch(batch_name: str, file_only: bool = False, *, quiet: bool = False) -> int:
    """Save whole hunk or file to batch and discard from working tree (internal helper)."""

    log_journal("discard_hunk_to_batch_start", batch_name=batch_name, file_only=file_only, quiet=quiet)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        log_journal("discard_hunk_to_batch_creating_batch", batch_name=batch_name)
        create_batch(batch_name, "Auto-created")

    # Ensure cached hunk is selected
    try:
        selected_item = fetch_next_change()
    except NoMoreHunks:
        print(_("No changes to process."), file=sys.stderr)
        return 0
    if isinstance(selected_item, BinaryFileChange):
        return _command_discard_binary_to_batch(batch_name, selected_item, quiet=quiet)

    # Get the file path and hash from currently cached hunk
    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    patch_bytes = read_file_bytes(get_selected_hunk_patch_file_path())
    line_changes = build_line_changes_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
    file_path = line_changes.path

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    file_mode = _detect_file_mode(file_path)

    # Collect all lines to batch (either selected hunk or all hunks from file)
    all_lines_to_batch = []
    all_display_ids_to_batch = set()
    patches_to_discard = []

    if file_only:
        # Collect ALL hunks from this file
        for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            if patch.new_path != file_path:
                continue

            patch_bytes_loop = patch.to_patch_bytes()
            patch_hash = compute_stable_hunk_hash(patch_bytes_loop)

            if patch_hash in blocked_hashes:
                continue

            # Parse hunk to get lines
            hunk_lines = build_line_changes_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
            all_lines_to_batch.extend(hunk_lines.lines)
            all_display_ids_to_batch.update(line.id for line in hunk_lines.lines if line.id is not None)
            patches_to_discard.append((patch_bytes_loop, patch_hash))
    else:
        # Just selected hunk (already loaded above)
        all_lines_to_batch = line_changes.lines
        all_display_ids_to_batch = {line.id for line in line_changes.lines if line.id is not None}
        patches_to_discard = [(patch_bytes, patch_hash)]

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if file_path in metadata.get("files", {}):
        # File already in batch - get existing ownership and batch source
        file_metadata = metadata["files"][file_path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=all_lines_to_batch
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        # Remapping failed - fail loudly
        exit_with_error(
            _("Cannot discard to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
        )

    # add_file_to_batch creates the batch source commit from this snapshot.
    snapshot_file_if_untracked(file_path)

    log_journal("discard_hunk_to_batch_before_add", batch_name=batch_name, file_path=file_path, num_patches=len(patches_to_discard))

    # Save to batch using batch source model (once, with all accumulated data)
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    log_journal("discard_hunk_to_batch_after_add", batch_name=batch_name, file_path=file_path)

    # Check if this is a new file (before applying patches)
    is_new_file = any(b"--- /dev/null" in patch_bytes_item for patch_bytes_item, _ in patches_to_discard)

    # Apply reverse patches to discard from working tree
    for patch_bytes_item, patch_hash in patches_to_discard:
        # Check if this is an empty file patch (@@ -0,0 +0,0 @@)
        # Empty file patches are synthetic and cannot be reversed with git apply
        is_empty_file_patch = b"@@ -0,0 +0,0 @@" in patch_bytes_item

        if not is_empty_file_patch:
            log_journal("discard_hunk_to_batch_before_git_apply", batch_name=batch_name, patch_hash=patch_hash, file_path=file_path, patch_content=patch_bytes_item.decode("utf-8", errors="replace"))

            # Use stream_command to apply reverse patch
            stderr_chunks = []
            exit_code = 0

            for event in stream_command(["git", "apply", "--reverse", "--unidiff-zero"], [patch_bytes_item]):
                if isinstance(event, ExitEvent):
                    exit_code = event.exit_code
                elif isinstance(event, OutputEvent):
                    if event.fd == 2:  # stderr
                        stderr_chunks.append(event.data)

            stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace') if stderr_chunks else ""
            log_journal("discard_hunk_to_batch_after_git_apply", batch_name=batch_name, patch_hash=patch_hash, exit_code=exit_code, stderr_len=len(stderr_text))

            if exit_code != 0:
                log_journal("discard_hunk_to_batch_git_apply_failed", batch_name=batch_name, patch_hash=patch_hash, exit_code=exit_code, stderr=stderr_text)
                exit_with_error(_("Failed to apply reverse patch: {error}").format(error=stderr_text))
        else:
            log_journal("discard_hunk_to_batch_skipping_empty_patch", batch_name=batch_name, patch_hash=patch_hash)
        # else: skip reverse for empty files - nothing to reverse, cleanup code below handles file removal

        # Mark this hunk as processed
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record hunk as discarded for progress tracking
        record_hunk_discarded(patch_hash)

    # Clean up file and index after discarding to batch
    # Only remove file if it's actually meant to be gone:
    # - New file that's now empty after reversal
    # - File deleted in diff (doesn't exist in working tree after reversal)
    if is_new_file or file_only:
        absolute_path = get_git_repository_root_path() / file_path

        # Check if file still exists after git apply --reverse
        if not absolute_path.exists():
            # File was deleted by reverse patches (was a file deletion diff)
            # Remove from index to complete the deletion
            run_git_command(["rm", "--cached", "--quiet", file_path], check=False)
        elif is_new_file:
            # New file: only remove if it's empty after reverse patches
            content = read_text_file_contents(absolute_path)
            if not content.strip():
                absolute_path.unlink()
                run_git_command(["rm", "--cached", "--quiet", file_path], check=False)
        # else: file still exists with content (reverted to HEAD state), leave it alone

    log_journal("discard_hunk_to_batch_success", batch_name=batch_name, file_path=file_path, num_patches=len(patches_to_discard))

    # Print success message
    if not quiet:
        if file_only:
            msg = ngettext(
                "✓ {count} hunk from {file} saved to batch '{name}' and discarded",
                "✓ {count} hunks from {file} saved to batch '{name}' and discarded",
                len(patches_to_discard)
            ).format(count=len(patches_to_discard), file=file_path, name=batch_name)
            print(msg, file=sys.stderr)
        else:
            print(_("✓ Hunk saved to batch '{name}' and discarded from working tree").format(name=batch_name), file=sys.stderr)

    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()
    return len(patches_to_discard)
