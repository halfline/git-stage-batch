"""Hunk navigation, selected-state orchestration, and progress tracking."""

from __future__ import annotations

import json
import subprocess
import sys
from enum import Enum
from typing import Optional, Union

from ..batch.attribution import build_file_attribution, filter_owned_diff_fragments
from ..batch.display import annotate_with_batch_source
from ..core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ..core.models import (
    BinaryFileChange,
    GitlinkChange,
    LineLevelChange,
    LineEntry,
    RenameChange,
    TextFileDeletionChange,
)
from ..core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
)
from ..core.line_selection import write_line_ids_file
from ..core.line_identity import preserve_line_ids_from_previous_view
from ..exceptions import CommandError, NoMoreHunks, exit_with_error
from ..i18n import _
from ..output import (
    print_line_level_changes,
    print_binary_file_change,
    print_gitlink_change,
    print_rename_change,
    print_text_file_deletion_change,
)
from .consumed_selections import read_consumed_file_metadata
from .auto_advance import resolve_auto_advance
from . import change_freshness as _change_freshness
from . import file_hunk_display as _file_hunk_display
from . import live_diff as _live_diff
from .progress import (
    format_id_range as format_id_range,
    record_binary_hunk_skipped as record_binary_hunk_skipped,
    record_gitlink_hunk_skipped as record_gitlink_hunk_skipped,
    record_hunk_discarded as record_hunk_discarded,
    record_hunk_included as record_hunk_included,
    record_hunk_skipped as record_hunk_skipped,
    record_hunks_discarded as record_hunks_discarded,
    record_rename_hunk_skipped as record_rename_hunk_skipped,
    record_text_deletion_hunk_skipped as record_text_deletion_hunk_skipped,
)
from .selected_change.snapshots import (
    snapshots_are_stale as snapshots_are_stale,
    write_snapshots_for_selected_file_path,
)
from ..utils.file_io import (
    is_path_blocked,
    read_file_paths_file,
    read_text_file_line_set,
    read_text_file_contents,
    write_text_file_contents,
)
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
)
from .line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from .batch_selected_changes import (
    compute_batch_binary_fingerprint as compute_batch_binary_fingerprint,
    compute_batch_gitlink_fingerprint as compute_batch_gitlink_fingerprint,
    require_current_selected_batch_binary_file_for_batch as require_current_selected_batch_binary_file_for_batch,
    require_current_selected_batch_gitlink_file_for_batch as require_current_selected_batch_gitlink_file_for_batch,
    selected_batch_binary_batch_name as selected_batch_binary_batch_name,
    selected_batch_binary_file_for_batch as selected_batch_binary_file_for_batch,
    selected_batch_binary_matches_batch as selected_batch_binary_matches_batch,
    selected_batch_gitlink_file_for_batch as selected_batch_gitlink_file_for_batch,
    selected_batch_gitlink_matches_batch as selected_batch_gitlink_matches_batch,
)
from .selected_change.lifecycle import clear_selected_change_state_files as clear_selected_change_state_files
from .selected_change.store import (
    SelectedChangeClearReason as SelectedChangeClearReason,
    SelectedChangeKind,
    SelectedChangeStateSnapshot as SelectedChangeStateSnapshot,
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rename_change,
    cache_text_deletion_change,
    get_selected_change_file_path as get_selected_change_file_path,
    load_line_changes_from_patch_path as _load_line_changes_from_patch_path,
    load_selected_binary_file,
    load_selected_gitlink_change,
    load_selected_rename_change,
    load_selected_text_deletion_change,
    mark_selected_change_cleared_by_auto_advance_disabled,
    mark_selected_change_cleared_by_file_list as mark_selected_change_cleared_by_file_list,
    read_selected_change_kind,
    refuse_bare_action_after_auto_advance_disabled as refuse_bare_action_after_auto_advance_disabled,
    refuse_bare_action_after_file_list as refuse_bare_action_after_file_list,
    refuse_bare_action_after_stale_batch_selection as refuse_bare_action_after_stale_batch_selection,
    restore_selected_change_state as restore_selected_change_state,
    selected_change_was_cleared_by_auto_advance_disabled as selected_change_was_cleared_by_auto_advance_disabled,
    selected_change_was_cleared_by_file_list as selected_change_was_cleared_by_file_list,
    selected_change_was_cleared_by_stale_batch_selection as selected_change_was_cleared_by_stale_batch_selection,
    snapshot_selected_change_state as snapshot_selected_change_state,
    write_line_changes_state as _write_line_changes_state,
    write_selected_change_kind,
    write_selected_hunk_patch_lines,
)


class RecalculateSelectedHunkResult(str, Enum):
    """Outcome from refreshing the selected hunk for one file."""

    RECALCULATED = "recalculated"
    CLEARED = "cleared"
    SHOW_NEXT_CHANGE = "show-next-change"


_BATCH_MERGE_REVIEW_ACTIONS = (
    "include-from-batch",
    "discard-from-batch",
    "apply-from-batch",
)
_BATCH_RESET_REVIEW_ACTION = "reset-from-batch"


def load_selected_change() -> Optional[Union[LineLevelChange, BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange]]:
    """Load the currently cached selected change, if any."""
    selected_kind = read_selected_change_kind()
    rename_change = load_selected_rename_change()
    if rename_change is not None:
        if (
            selected_kind == SelectedChangeKind.RENAME
            and _change_freshness.rename_change_is_stale(rename_change)
        ):
            raise CommandError(
                _(
                    "Selected rename no longer matches the working tree: {old} -> {new}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(old=rename_change.old_path, new=rename_change.new_path)
            )
        return rename_change

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        if (
            selected_kind == SelectedChangeKind.DELETION
            and _change_freshness.text_deletion_change_is_stale(deletion_change)
        ):
            raise CommandError(
                _(
                    "Selected text file deletion no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=deletion_change.path())
            )
        return deletion_change

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        if (
            selected_kind == SelectedChangeKind.GITLINK
            and _change_freshness.gitlink_change_is_stale(gitlink_change)
        ):
            raise CommandError(
                _(
                    "Selected submodule pointer no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=gitlink_change.path())
            )
        return gitlink_change

    binary_file = load_selected_binary_file()
    if binary_file is not None:
        if (
            selected_kind == SelectedChangeKind.BINARY
            and _change_freshness.binary_file_change_is_stale(binary_file)
        ):
            file_path = binary_file.new_path if binary_file.new_path != "/dev/null" else binary_file.old_path
            raise CommandError(
                _(
                    "Selected binary file no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=file_path)
            )
        return binary_file

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    require_selected_hunk()

    line_changes = load_line_changes_from_state()
    if line_changes is not None:
        return line_changes

    return _load_line_changes_from_patch_path(patch_path)


def apply_line_level_batch_filter_to_cached_hunk() -> bool:
    """Filter cached hunk using file-centric ownership attribution.

    File-centric blame-like approach:
    1. Build complete file attribution (all ownership-relevant units + batch owners)
    2. Project attribution onto diff fragments
    3. Filter owned fragments

    Returns:
        True if hunk should be skipped (all lines filtered), False otherwise
    """
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return True

    file_path = line_changes.path

    if (
        not line_changes.lines
        and _change_freshness.empty_text_lifecycle_change_is_batched(file_path)
    ):
        return True

    # Step 1: Build file attribution (file-centric, not diff-centric)
    attribution = build_file_attribution(file_path)

    # Step 2 & 3: Project to diff and filter owned fragments
    should_skip, filtered_line_changes = filter_owned_diff_fragments(
        line_changes, attribution
    )

    if should_skip:
        return True

    filtered_line_changes = _filter_consumed_replacement_masks(filtered_line_changes)
    if filtered_line_changes is None:
        return True

    # Update cached hunk with filtered version
    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(convert_line_changes_to_serializable_dict(filtered_line_changes),
                  ensure_ascii=False, indent=0)
    )

    return False


def _filter_consumed_replacement_masks(
    line_changes: LineLevelChange,
) -> LineLevelChange | None:
    """Hide synthetic replacement runs created by `include --line --as`."""
    file_metadata = read_consumed_file_metadata(line_changes.path)
    replacement_masks = file_metadata.get("replacement_masks", []) if file_metadata else []
    if not replacement_masks:
        return line_changes

    normalized_masks: set[tuple[tuple[str, str], ...]] = set()
    for mask in replacement_masks:
        deleted_signature = tuple(("-", text) for text in mask.get("deleted_lines", []))
        added_signature = tuple(("+", text) for text in mask.get("added_lines", []))
        full_signature = deleted_signature + added_signature
        if full_signature:
            normalized_masks.add(full_signature)
        if deleted_signature:
            normalized_masks.add(deleted_signature)
        if added_signature:
            normalized_masks.add(added_signature)

    filtered_lines = []
    changed_run: list[LineEntry] = []

    def flush_changed_run() -> None:
        nonlocal changed_run
        if not changed_run:
            return
        run_signature = tuple(
            (line.kind, line.display_text())
            for line in changed_run
            if line.kind in ("+", "-")
        )
        if run_signature not in normalized_masks:
            filtered_lines.extend(changed_run)
        changed_run = []

    for line_entry in line_changes.lines:
        if line_entry.kind in ("+", "-"):
            changed_run.append(line_entry)
            continue
        flush_changed_run()
        filtered_lines.append(line_entry)

    flush_changed_run()

    has_changes_after_filter = any(line.kind in ("+", "-") for line in filtered_lines)
    if not has_changes_after_filter:
        return None

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=filtered_lines,
    )


def fetch_next_change() -> Union[LineLevelChange, BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange]:
    """Find the next hunk or binary file that isn't blocked and cache it as selected.

    Returns:
        LineLevelChange for text hunks, BinaryFileChange for binary files.

    Raises:
        NoMoreHunks: When there are no more items to process.
    """
    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist (includes selected iteration)
    blocked_hashes = read_text_file_line_set(get_block_list_file_path())

    # Stream git diff and parse incrementally - stops after first unblocked item found
    try:
        with acquire_unified_diff(
            _live_diff.stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for item in patches:
                if isinstance(item, RenameChange):
                    rename_hash = compute_rename_change_hash(item)
                    if rename_hash in blocked_hashes:
                        continue

                    if (
                        is_path_blocked(item.old_path, blocked_files)
                        or is_path_blocked(item.new_path, blocked_files)
                    ):
                        continue

                    cache_rename_change(item)
                    return item

                if isinstance(item, TextFileDeletionChange):
                    deletion_hash = compute_text_file_deletion_hash(item)
                    if (
                        deletion_hash in blocked_hashes
                        or _change_freshness.text_deletion_change_is_batched(item)
                    ):
                        continue

                    if is_path_blocked(item.path(), blocked_files):
                        continue

                    cache_text_deletion_change(item)
                    return item

                if isinstance(item, GitlinkChange):
                    gitlink_hash = compute_gitlink_change_hash(item)
                    if gitlink_hash in blocked_hashes:
                        continue

                    if is_path_blocked(item.path(), blocked_files):
                        continue

                    cache_gitlink_change(item)
                    return item

                # Handle binary files
                if isinstance(item, BinaryFileChange):
                    binary_hash = compute_binary_file_hash(item)
                    if binary_hash in blocked_hashes:
                        continue

                    # Determine file path for blocked files check
                    file_path = item.new_path if item.new_path != "/dev/null" else item.old_path
                    if is_path_blocked(file_path, blocked_files):
                        continue

                    cache_binary_file_change(item)

                    # Return the BinaryFileChange object directly
                    return item

                # Handle text hunks (SingleHunkPatch)
                if item.old_path != item.new_path:
                    rename_hash = compute_rename_change_hash(
                        RenameChange(old_path=item.old_path, new_path=item.new_path)
                    )
                    if rename_hash in blocked_hashes:
                        continue

                hunk_hash = compute_stable_hunk_hash_from_lines(item.lines)
                if hunk_hash in blocked_hashes:
                    continue

                # Skip hunks from blocked files
                line_changes = build_line_changes_from_patch_lines(
                    item.lines,
                    annotator=annotate_with_batch_source,
                )
                if is_path_blocked(line_changes.path, blocked_files):
                    continue

                write_selected_hunk_patch_lines(item.lines)
                write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)
                write_selected_change_kind(SelectedChangeKind.HUNK)

                write_text_file_contents(get_line_changes_json_file_path(),
                                         json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                                    ensure_ascii=False, indent=0))
                write_snapshots_for_selected_file_path(line_changes.path)

                # Apply line-level batch filtering
                if apply_line_level_batch_filter_to_cached_hunk():
                    # All lines were batched, skip this hunk and continue
                    clear_selected_change_state_files()
                    continue

                # Return filtered hunk (or original if no filtering applied)
                return load_line_changes_from_state()
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    # No more items to process
    raise NoMoreHunks()


def advance_to_next_change() -> None:
    """Clear selected hunk state and advance to the next unblocked hunk.

    If no more hunks exist, clears state and returns silently.
    """
    clear_selected_change_state_files()
    try:
        fetch_next_change()
    except NoMoreHunks:
        # No more items - state is already cleared
        pass


def show_selected_change() -> None:
    """Display the currently cached hunk or binary file.

    This is a helper for commands that need to display the cached hunk
    without advancing (e.g., start, again).
    """
    rename_change = load_selected_rename_change()
    if rename_change is not None:
        print_rename_change(rename_change)
        return

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        print_text_file_deletion_change(deletion_change)
        return

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        print_gitlink_change(gitlink_change)
        return

    # Check if selected item is a binary file
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        print_binary_file_change(binary_file)
        return

    # Otherwise, show text hunk
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        line_changes = _load_line_changes_from_patch_path(patch_path)
        print_line_level_changes(line_changes)


def advance_to_and_show_next_change() -> None:
    """Advance to next hunk/binary file and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk/binary file with displaying it. If no more items exist,
    prints a message to stderr.
    """
    advance_to_next_change()

    rename_change = load_selected_rename_change()
    if rename_change is not None:
        print_rename_change(rename_change)
        return

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        print_text_file_deletion_change(deletion_change)
        return

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        print_gitlink_change(gitlink_change)
        return

    # Check if a binary file was cached
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        print_binary_file_change(binary_file)
        return

    # Check if a text hunk was cached
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        line_changes = _load_line_changes_from_patch_path(patch_path)
        print_line_level_changes(line_changes)
    else:
        print(_("No more hunks to process."), file=sys.stderr)


def finish_selected_change_action(
    *,
    quiet: bool,
    auto_advance: bool | None = None,
) -> None:
    """Apply the configured selection step after a hunk action completes."""
    if not select_next_change_after_action(auto_advance=auto_advance):
        return

    if quiet:
        return

    if read_selected_change_kind() is None:
        print(_("No more hunks to process."), file=sys.stderr)
        return

    show_selected_change()


def select_next_change_after_action(
    *,
    auto_advance: bool | None = None,
) -> bool:
    """Select the next hunk after an action, or leave selection empty."""
    if resolve_auto_advance(auto_advance):
        advance_to_next_change()
        return True

    clear_selected_change_state_files()
    mark_selected_change_cleared_by_auto_advance_disabled()
    return False




def require_selected_hunk() -> None:
    """Ensure selected hunk exists and is not stale, exit with error otherwise."""
    if read_selected_change_kind() in (SelectedChangeKind.BATCH_FILE, SelectedChangeKind.BATCH_BINARY):
        exit_with_error(
            _(
                "Selected file came from a batch, not a live hunk. "
                "Open a live hunk with 'show' or use the matching '--from' command."
            )
        )

    if not get_selected_hunk_patch_file_path().exists():
        exit_with_error(_("No selected hunk. Run 'start' first."))

    if get_line_changes_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_line_changes_json_file_path()))
        file_path = data["path"]
        if snapshots_are_stale(file_path):
            clear_selected_change_state_files()
            exit_with_error(_("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."))


def recalculate_selected_hunk_for_file(
    file_path: str,
    *,
    auto_advance: bool | None = None,
) -> RecalculateSelectedHunkResult:
    """Recalculate the selected hunk for a specific file after modifications.

    After discard --line or include --line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.

    Args:
        file_path: Repository-relative path to recalculate hunk for
    """
    selected_kind = read_selected_change_kind()
    previous_line_changes = load_line_changes_from_state()
    if previous_line_changes is not None and previous_line_changes.path != file_path:
        previous_line_changes = None

    # Clear processed IDs since old line numbers don't apply to fresh hunk
    write_line_ids_file(get_processed_include_ids_file_path(), set())
    write_line_ids_file(get_processed_skip_ids_file_path(), set())

    if selected_kind == SelectedChangeKind.FILE:
        line_changes = _file_hunk_display.cache_unstaged_file_as_single_hunk(file_path)
        if line_changes is None:
            clear_selected_change_state_files()
            if resolve_auto_advance(auto_advance):
                return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
            mark_selected_change_cleared_by_auto_advance_disabled()
            return RecalculateSelectedHunkResult.CLEARED

        line_changes = preserve_line_ids_from_previous_view(
            previous_line_changes,
            line_changes,
        )
        _write_line_changes_state(line_changes)

        if apply_line_level_batch_filter_to_cached_hunk():
            clear_selected_change_state_files()
            if resolve_auto_advance(auto_advance):
                return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
            mark_selected_change_cleared_by_auto_advance_disabled()
            return RecalculateSelectedHunkResult.CLEARED

        line_changes = load_line_changes_from_state()
        if line_changes is not None:
            print_line_level_changes(line_changes)
        return RecalculateSelectedHunkResult.RECALCULATED

    # Load blocklist
    blocked_hashes = read_text_file_line_set(get_block_list_file_path())

    # Stream git diff and parse incrementally - stops after first matching hunk found
    try:
        with acquire_unified_diff(
            _live_diff.stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for single_hunk in patches:
                if single_hunk.old_path != file_path and single_hunk.new_path != file_path:
                    continue

                if isinstance(single_hunk, RenameChange):
                    rename_hash = compute_rename_change_hash(single_hunk)
                    if rename_hash in blocked_hashes:
                        continue
                    cache_rename_change(single_hunk)
                    print_rename_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, TextFileDeletionChange):
                    deletion_hash = compute_text_file_deletion_hash(single_hunk)
                    if (
                        deletion_hash in blocked_hashes
                        or _change_freshness.text_deletion_change_is_batched(
                            single_hunk
                        )
                    ):
                        continue
                    cache_text_deletion_change(single_hunk)
                    print_text_file_deletion_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, GitlinkChange):
                    gitlink_hash = compute_gitlink_change_hash(single_hunk)
                    if gitlink_hash in blocked_hashes:
                        continue
                    cache_gitlink_change(single_hunk)
                    print_gitlink_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, BinaryFileChange):
                    continue

                if single_hunk.old_path != single_hunk.new_path:
                    rename_hash = compute_rename_change_hash(
                        RenameChange(
                            old_path=single_hunk.old_path,
                            new_path=single_hunk.new_path,
                        )
                    )
                    if rename_hash in blocked_hashes:
                        continue

                hunk_hash = compute_stable_hunk_hash_from_lines(single_hunk.lines)

                if hunk_hash in blocked_hashes:
                    continue

                write_selected_hunk_patch_lines(single_hunk.lines)
                write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)
                write_selected_change_kind(SelectedChangeKind.HUNK)

                line_changes = build_line_changes_from_patch_lines(
                    single_hunk.lines,
                    annotator=annotate_with_batch_source,
                )
                line_changes = preserve_line_ids_from_previous_view(
                    previous_line_changes,
                    line_changes,
                )
                _write_line_changes_state(line_changes)
                write_snapshots_for_selected_file_path(line_changes.path)

                # Apply batch filter to exclude batched lines
                if apply_line_level_batch_filter_to_cached_hunk():
                    # All lines were batched, clear the hunk
                    clear_selected_change_state_files()
                    print(_("No more lines in this hunk."), file=sys.stderr)
                    return RecalculateSelectedHunkResult.CLEARED

                # Display filtered hunk
                line_changes = load_line_changes_from_state()
                if line_changes is not None:
                    print_line_level_changes(line_changes)
                return RecalculateSelectedHunkResult.RECALCULATED
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        clear_selected_change_state_files()
        print(_("No pending hunks."), file=sys.stderr)
        return RecalculateSelectedHunkResult.CLEARED

    # No more hunks for this file, advance to next file
    clear_selected_change_state_files()
    if resolve_auto_advance(auto_advance):
        return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
    mark_selected_change_cleared_by_auto_advance_disabled()
    return RecalculateSelectedHunkResult.CLEARED
