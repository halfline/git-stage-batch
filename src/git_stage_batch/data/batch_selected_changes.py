"""Selected atomic batch changes and stale-selection validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256

from ..batch.query import get_batch_commit_sha
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.git_command import run_git_command
from .selected_change.lifecycle import clear_selected_change_state_files
from .selected_change.file_changes import (
    load_selected_binary_file,
    load_selected_gitlink_change,
    read_selected_binary_data,
    read_selected_gitlink_data,
)
from .selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from .selected_change.clear_reasons import (
    mark_selected_change_cleared_by_stale_batch_selection,
)


def compute_batch_binary_fingerprint(
    batch_name: str,
    file_path: str,
    file_meta: Mapping[str, object],
) -> str:
    """Return a stable identity for the current binary content stored in a batch."""
    batch_blob = None
    if file_meta.get("change_type") != "deleted":
        batch_commit = get_batch_commit_sha(batch_name)
        if batch_commit is not None:
            blob_result = run_git_command(
                ["rev-parse", "--verify", f"{batch_commit}:{file_path}"],
                check=False,
                requires_index_lock=False,
            )
            if blob_result.returncode == 0:
                batch_blob = blob_result.stdout.strip()

    payload = {
        "file_path": file_path,
        "file_type": file_meta.get("file_type"),
        "change_type": file_meta.get("change_type"),
        "mode": file_meta.get("mode"),
        "batch_source_commit": file_meta.get("batch_source_commit"),
        "batch_blob": batch_blob,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8", errors="surrogateescape")).hexdigest()


def selected_batch_binary_matches_batch(batch_name: str) -> bool:
    """Return whether the cached batch-binary selection came from this batch."""
    if read_selected_change_kind() != SelectedChangeKind.BATCH_BINARY:
        return False
    binary_data = read_selected_binary_data()
    if binary_data is None:
        return False
    return binary_data.get("batch_name") == batch_name


def selected_batch_binary_batch_name() -> str | None:
    """Return the source batch name for the cached batch-binary selection."""
    if read_selected_change_kind() != SelectedChangeKind.BATCH_BINARY:
        return None
    binary_data = read_selected_binary_data()
    if binary_data is None:
        return None
    batch_name = binary_data.get("batch_name")
    return batch_name if isinstance(batch_name, str) else None


def selected_batch_binary_file_for_batch(
    batch_name: str,
    all_files: Mapping[str, dict],
) -> str | None:
    """Return the selected batch-binary file if it still exists in batch metadata."""
    if not selected_batch_binary_matches_batch(batch_name):
        return None

    binary_file = load_selected_binary_file()
    if binary_file is None:
        return None

    file_path = binary_file.new_path if binary_file.new_path != "/dev/null" else binary_file.old_path
    file_meta = all_files.get(file_path)
    if file_meta is None:
        return None
    if file_meta.get("file_type") != "binary":
        return None
    if file_meta.get("change_type") != binary_file.change_type:
        return None

    binary_data = read_selected_binary_data()
    if binary_data is None:
        return None
    cached_fingerprint = binary_data.get("batch_binary_fingerprint")
    if not isinstance(cached_fingerprint, str):
        return None
    current_fingerprint = compute_batch_binary_fingerprint(batch_name, file_path, file_meta)
    if current_fingerprint != cached_fingerprint:
        return None

    return file_path


def require_current_selected_batch_binary_file_for_batch(
    batch_name: str,
    all_files: Mapping[str, dict],
) -> str | None:
    """Return selected batch-binary file for this batch, or refuse if it went stale."""
    if not selected_batch_binary_matches_batch(batch_name):
        return None

    selected_file = selected_batch_binary_file_for_batch(batch_name, all_files)
    if selected_file is not None:
        return selected_file

    binary_file = load_selected_binary_file()
    file_path = (
        binary_file.new_path
        if binary_file is not None and binary_file.new_path != "/dev/null" else
        binary_file.old_path
        if binary_file is not None else
        "the selected batch binary"
    )
    clear_selected_change_state_files()
    mark_selected_change_cleared_by_stale_batch_selection(
        batch_name=batch_name,
        file_path=file_path,
    )
    exit_with_error(
        _(
            "The selected batch binary no longer matches batch '{name}'.\n"
            "Show the batch again before using a pathless batch action."
        ).format(name=batch_name)
    )


def compute_batch_gitlink_fingerprint(
    file_path: str,
    file_meta: Mapping[str, object],
) -> str:
    """Return a stable identity for the current stored submodule pointer."""
    payload = {
        "file_path": file_path,
        "file_type": file_meta.get("file_type"),
        "change_type": file_meta.get("change_type"),
        "mode": file_meta.get("mode"),
        "old_oid": file_meta.get("old_oid"),
        "new_oid": file_meta.get("new_oid"),
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8", errors="surrogateescape")).hexdigest()


def selected_batch_gitlink_matches_batch(batch_name: str) -> bool:
    """Return whether the cached batch submodule pointer came from this batch."""
    if read_selected_change_kind() != SelectedChangeKind.BATCH_GITLINK:
        return False
    gitlink_data = read_selected_gitlink_data()
    if gitlink_data is None:
        return False
    return gitlink_data.get("batch_name") == batch_name


def selected_batch_gitlink_file_for_batch(
    batch_name: str,
    all_files: Mapping[str, dict],
) -> str | None:
    """Return the selected batch submodule pointer if it is still current."""
    if not selected_batch_gitlink_matches_batch(batch_name):
        return None

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is None:
        return None

    file_path = gitlink_change.path()
    file_meta = all_files.get(file_path)
    if file_meta is None:
        return None
    if file_meta.get("file_type") != "gitlink":
        return None
    if file_meta.get("change_type") != gitlink_change.change_type:
        return None
    if file_meta.get("old_oid") != gitlink_change.old_oid:
        return None
    if file_meta.get("new_oid") != gitlink_change.new_oid:
        return None

    gitlink_data = read_selected_gitlink_data()
    if gitlink_data is None:
        return None
    cached_fingerprint = gitlink_data.get("batch_gitlink_fingerprint")
    if not isinstance(cached_fingerprint, str):
        return None
    current_fingerprint = compute_batch_gitlink_fingerprint(file_path, file_meta)
    if current_fingerprint != cached_fingerprint:
        return None

    return file_path


def require_current_selected_batch_gitlink_file_for_batch(
    batch_name: str,
    all_files: Mapping[str, dict],
) -> str | None:
    """Return selected batch submodule pointer, or refuse if it went stale."""
    if not selected_batch_gitlink_matches_batch(batch_name):
        return None

    selected_file = selected_batch_gitlink_file_for_batch(batch_name, all_files)
    if selected_file is not None:
        return selected_file

    gitlink_change = load_selected_gitlink_change()
    file_path = gitlink_change.path() if gitlink_change is not None else "the selected batch submodule pointer"
    clear_selected_change_state_files()
    mark_selected_change_cleared_by_stale_batch_selection(
        batch_name=batch_name,
        file_path=file_path,
    )
    exit_with_error(
        _(
            "The selected batch submodule pointer no longer matches batch '{name}'.\n"
            "Show the batch again before using a pathless batch action."
        ).format(name=batch_name)
    )
