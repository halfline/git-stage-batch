"""Again command implementation."""

from __future__ import annotations

import shutil
import tempfile

from ..data.file_tracking import auto_add_untracked_files
from ..data.session import require_session_started
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import require_git_repository
from ..utils.paths import (
    ensure_state_directory_exists,
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_batches_directory_path,
    get_state_directory_path,
)


def command_again() -> None:
    """Clear state and start a fresh pass through all hunks."""
    require_git_repository()
    require_session_started()
    state_dir = get_state_directory_path()

    # Preserve batch directories, abort state across state wipe
    temp_batches_dir = None
    abort_head_content = ""
    abort_stash_content = ""
    abort_snapshot_list_content = ""
    temp_snapshots_dir = None

    if state_dir.exists():
        # Preserve batch directory structure (contains per-batch claims)
        batches_dir = get_batches_directory_path()
        if batches_dir.exists():
            temp_batches_dir = tempfile.mkdtemp(prefix="git-stage-batch-batches-")
            shutil.copytree(batches_dir, temp_batches_dir, dirs_exist_ok=True)

        # Preserve abort state files
        abort_head_file = get_abort_head_file_path()
        if abort_head_file.exists():
            abort_head_content = read_text_file_contents(abort_head_file)

        abort_stash_file = get_abort_stash_file_path()
        if abort_stash_file.exists():
            abort_stash_content = read_text_file_contents(abort_stash_file)

        abort_snapshot_list_file = get_abort_snapshot_list_file_path()
        if abort_snapshot_list_file.exists():
            abort_snapshot_list_content = read_text_file_contents(abort_snapshot_list_file)

        # Preserve snapshots directory
        snapshots_dir = get_abort_snapshots_directory_path()
        if snapshots_dir.exists():
            temp_snapshots_dir = tempfile.mkdtemp(prefix="git-stage-batch-snapshots-")
            shutil.copytree(snapshots_dir, temp_snapshots_dir, dirs_exist_ok=True)

        shutil.rmtree(state_dir)

    ensure_state_directory_exists()

    # Restore batch directories
    if temp_batches_dir:
        batches_dir = get_batches_directory_path()
        shutil.copytree(temp_batches_dir, batches_dir, dirs_exist_ok=True)
        shutil.rmtree(temp_batches_dir)
        # Recompute global masks from per-batch claims
        from ..batch.mask import recompute_global_batch_mask
        recompute_global_batch_mask()

    # Restore abort state
    if abort_head_content:
        write_text_file_contents(get_abort_head_file_path(), abort_head_content)
    if abort_stash_content:
        write_text_file_contents(get_abort_stash_file_path(), abort_stash_content)
    if abort_snapshot_list_content:
        write_text_file_contents(get_abort_snapshot_list_file_path(), abort_snapshot_list_content)
    if temp_snapshots_dir:
        snapshots_dir = get_abort_snapshots_directory_path()
        shutil.copytree(temp_snapshots_dir, snapshots_dir, dirs_exist_ok=True)
        shutil.rmtree(temp_snapshots_dir)

    # Auto-add untracked files for fresh pass
    auto_add_untracked_files()
