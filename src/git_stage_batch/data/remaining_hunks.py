"""Remaining hunk estimation for session progress."""

from __future__ import annotations

from ..core.diff_parser import acquire_unified_diff
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
    RenameChange,
    TextFileDeletionChange,
)
from .change_freshness import text_deletion_change_is_batched
from .live_diff import stream_live_git_diff
from ..utils.file_io import (
    is_path_blocked,
    read_file_paths_file,
    read_text_file_line_set,
)
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
)


def estimate_remaining_hunks() -> int:
    """Estimate the number of live hunks not yet included, skipped, or discarded."""
    blocked_hashes = read_text_file_line_set(get_block_list_file_path())
    blocked_files = read_file_paths_file(get_blocked_files_file_path())

    remaining = 0
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for patch in patches:
                remaining += _unprocessed_patch_count(
                    patch,
                    blocked_hashes=blocked_hashes,
                    blocked_files=blocked_files,
                )
    except Exception:
        return 0

    return remaining


def _unprocessed_patch_count(
    patch,
    *,
    blocked_hashes: set[str],
    blocked_files: set[str],
) -> int:
    if isinstance(patch, RenameChange):
        hunk_hash = compute_rename_change_hash(patch)
        if hunk_hash in blocked_hashes:
            return 0
        if is_path_blocked(patch.old_path, blocked_files):
            return 0
        return 0 if is_path_blocked(patch.new_path, blocked_files) else 1

    if isinstance(patch, TextFileDeletionChange):
        if text_deletion_change_is_batched(patch):
            return 0
        hunk_hash = compute_text_file_deletion_hash(patch)
        file_path = patch.path()
    elif isinstance(patch, GitlinkChange):
        hunk_hash = compute_gitlink_change_hash(patch)
        file_path = patch.path()
    elif isinstance(patch, BinaryFileChange):
        hunk_hash = compute_binary_file_hash(patch)
        file_path = patch.path()
    else:
        if patch.old_path != patch.new_path:
            rename_hash = compute_rename_change_hash(
                RenameChange(old_path=patch.old_path, new_path=patch.new_path)
            )
            if rename_hash in blocked_hashes:
                return 0
        hunk_hash = compute_stable_hunk_hash_from_lines(patch.lines)
        file_path = patch.old_path if patch.old_path != "/dev/null" else patch.new_path

    file_path = file_path.removeprefix("a/").removeprefix("b/")

    if hunk_hash in blocked_hashes:
        return 0
    return 0 if is_path_blocked(file_path, blocked_files) else 1
