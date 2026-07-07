"""Hunk navigation, selected-state orchestration, and progress tracking."""

from __future__ import annotations

import json
import subprocess
from typing import Union

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
    RenameChange,
    TextFileDeletionChange,
)
from ..core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
)
from ..exceptions import NoMoreHunks
from .auto_advance import resolve_auto_advance
from . import change_freshness as _change_freshness
from . import live_diff as _live_diff
from .selected_change import store as _selected_store
from .selected_change import hunk_filtering as _selected_hunk_filtering
from .selected_change.snapshots import (
    write_snapshots_for_selected_file_path as _write_snapshots_for_selected_file_path,
)
from ..utils.file_io import (
    is_path_blocked,
    read_file_paths_file,
    read_text_file_line_set,
    write_text_file_contents,
)
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_line_changes_json_file_path,
)
from . import line_state as _line_state
from .selected_change.lifecycle import (
    clear_selected_change_state_files as _clear_selected_change_state_files,
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

                    _selected_store.cache_rename_change(item)
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

                    _selected_store.cache_text_deletion_change(item)
                    return item

                if isinstance(item, GitlinkChange):
                    gitlink_hash = compute_gitlink_change_hash(item)
                    if gitlink_hash in blocked_hashes:
                        continue

                    if is_path_blocked(item.path(), blocked_files):
                        continue

                    _selected_store.cache_gitlink_change(item)
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

                    _selected_store.cache_binary_file_change(item)

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

                _selected_store.write_selected_hunk_patch_lines(item.lines)
                write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)
                _selected_store.write_selected_change_kind(
                    _selected_store.SelectedChangeKind.HUNK
                )

                write_text_file_contents(
                    get_line_changes_json_file_path(),
                    json.dumps(
                        _line_state.convert_line_changes_to_serializable_dict(
                            line_changes
                        ),
                        ensure_ascii=False,
                        indent=0,
                    ),
                )
                _write_snapshots_for_selected_file_path(line_changes.path)

                # Apply line-level batch filtering
                if (
                    _selected_hunk_filtering.apply_line_level_batch_filter_to_cached_hunk()
                ):
                    # All lines were batched, skip this hunk and continue
                    _clear_selected_change_state_files()
                    continue

                # Return filtered hunk (or original if no filtering applied)
                return _line_state.load_line_changes_from_state()
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    # No more items to process
    raise NoMoreHunks()


def advance_to_next_change() -> None:
    """Clear selected hunk state and advance to the next unblocked hunk.

    If no more hunks exist, clears state and returns silently.
    """
    _clear_selected_change_state_files()
    try:
        fetch_next_change()
    except NoMoreHunks:
        # No more items - state is already cleared
        pass


def select_next_change_after_action(
    *,
    auto_advance: bool | None = None,
) -> bool:
    """Select the next hunk after an action, or leave selection empty."""
    if resolve_auto_advance(auto_advance):
        advance_to_next_change()
        return True

    _clear_selected_change_state_files()
    _selected_store.mark_selected_change_cleared_by_auto_advance_disabled()
    return False
