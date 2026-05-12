"""Skip command implementation."""

from __future__ import annotations

from dataclasses import replace
import json
import sys

from ..core.diff_parser import build_line_changes_from_patch_lines, parse_unified_diff_streaming
from ..core.hashing import compute_binary_file_hash, compute_stable_hunk_hash_from_lines
from ..core.line_selection import (
    parse_line_selection,
    read_line_ids_file,
    write_line_ids_file,
)
from ..batch.selection import require_line_selection_in_view
from ..core.models import BinaryFileChange
from ..data.hunk_tracking import (
    SelectedChangeKind,
    advance_to_and_show_next_change,
    advance_to_next_change,
    fetch_next_change,
    get_selected_change_file_path,
    load_selected_change,
    read_selected_change_kind,
    record_binary_hunk_skipped,
    record_hunk_skipped,
    refuse_bare_action_after_file_list,
    require_selected_hunk,
)
from ..data.file_review_state import (
    FileReviewAction,
    finish_review_scoped_line_action,
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
    resolve_live_line_action_scope,
)
from ..data.line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from ..data.session import require_session_started
from ..data.undo import undo_checkpoint
from ..exceptions import NoMoreHunks, exit_with_error
from ..i18n import _, ngettext
from ..output import print_line_level_changes, print_remaining_line_changes_header
from ..utils.file_io import (
    append_lines_to_file,
    read_text_file_line_set,
    read_text_file_contents,
    write_text_file_contents,
)
from ..utils.git import require_git_repository, stream_git_diff
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
    get_processed_skip_ids_file_path,
)


def command_skip(*, quiet: bool = False) -> None:
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

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        command_skip_file("")
        return

    item = load_selected_change()
    if item is None:
        try:
            item = fetch_next_change()
        except NoMoreHunks:
            if not quiet:
                print(_("No more hunks to process."), file=sys.stderr)
            return
    with undo_checkpoint("skip"):
        # Read cached hash
        patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()

        # Handle based on item type
        if isinstance(item, BinaryFileChange):
            # Binary file - just add to blocklist
            file_path = item.new_path if item.new_path != "/dev/null" else item.old_path

            # Add hash to blocklist (without staging)
            blocklist_path = get_block_list_file_path()
            append_lines_to_file(blocklist_path, [patch_hash])

            record_binary_hunk_skipped(item, patch_hash)

            if not quiet:
                change_desc = "added" if item.is_new_file() else ("deleted" if item.is_deleted_file() else "modified")
                print(_("✓ Binary file {desc} skipped: {file}").format(desc=change_desc, file=file_path), file=sys.stderr)

            if quiet:
                advance_to_next_change()
            else:
                advance_to_and_show_next_change()
            return

        # Text hunk - item is LineLevelChange here
        filename = item.path

        # Add hash to blocklist (without staging)
        blocklist_path = get_block_list_file_path()
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record for progress tracking
        record_hunk_skipped(item, patch_hash)

        if not quiet:
            print(_("✓ Hunk skipped from {file}").format(file=filename), file=sys.stderr)

        if quiet:
            advance_to_next_change()
        else:
            advance_to_and_show_next_change()


def command_skip_file(
    file: str = "",
    *,
    quiet: bool = False,
    advance: bool = True,
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

    # Determine target file
    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            if not quiet:
                print(_("No selected hunk. Run 'show' first or specify file path."), file=sys.stderr)
            return 0
    else:
        target_file = file

    with undo_checkpoint(f"skip --file {file}".rstrip()):
        # Stream through hunks and skip all from target file.
        blocklist_path = get_block_list_file_path()
        blocked_hashes = read_text_file_line_set(blocklist_path)

        hunks_skipped = 0
        for patch in parse_unified_diff_streaming(stream_git_diff(context_lines=get_context_lines())):
            if isinstance(patch, BinaryFileChange):
                file_path = patch.new_path if patch.new_path != "/dev/null" else patch.old_path
                if file_path != target_file:
                    continue

                patch_hash = compute_binary_file_hash(patch)
                if patch_hash in blocked_hashes:
                    continue

                append_lines_to_file(blocklist_path, [patch_hash])
                blocked_hashes.add(patch_hash)
                record_binary_hunk_skipped(patch, patch_hash)
                hunks_skipped += 1
                continue

            if patch.new_path != target_file:
                continue

            patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)

            # Skip if already blocked
            if patch_hash in blocked_hashes:
                continue

            # Add to blocklist without staging
            append_lines_to_file(blocklist_path, [patch_hash])
            blocked_hashes.add(patch_hash)
            record_hunk_skipped(
                build_line_changes_from_patch_lines(patch.lines),
                patch_hash,
            )
            hunks_skipped += 1

        if quiet and advance:
            advance_to_next_change()
        if quiet:
            return hunks_skipped

        msg = ngettext(
            "✓ Skipped {count} hunk from {file}",
            "✓ Skipped {count} hunks from {file}",
            hunks_skipped
        ).format(count=hunks_skipped, file=target_file)
        print(msg, file=sys.stderr)

        advance_to_and_show_next_change()
        return hunks_skipped


def command_skip_line(line_id_specification: str, file: str | None = None) -> None:
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
        from ..data.hunk_tracking import cache_unstaged_file_as_single_hunk
        from ..data.hunk_tracking import render_unstaged_file_as_single_hunk

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
                command_skip_file("")
                return

            patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
            blocklist_path = get_block_list_file_path()
            append_lines_to_file(blocklist_path, [patch_hash])
            record_hunk_skipped(line_changes, patch_hash)
            print(_("✓ Skipped line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)
            finish_review_scoped_line_action(review_state)
            advance_to_and_show_next_change()
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
