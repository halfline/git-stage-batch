"""Selected file-change JSON persistence."""

from __future__ import annotations

import json

from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_text_file_deletion_hash,
)
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import (
    get_index_snapshot_file_path,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_selected_binary_file_json_path,
    get_selected_gitlink_file_json_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_selected_rename_file_json_path,
    get_selected_text_deletion_file_json_path,
    get_working_tree_snapshot_file_path,
)
from .store import (
    SelectedChangeKind,
    read_selected_change_kind,
    write_selected_change_kind,
)
from .snapshots import write_snapshots_for_selected_file_path


def read_selected_binary_data() -> dict | None:
    """Read cached binary selection data, if structurally valid."""
    binary_path = get_selected_binary_file_json_path()
    if not binary_path.exists():
        return None
    try:
        binary_data = json.loads(read_text_file_contents(binary_path))
    except json.JSONDecodeError:
        return None
    return binary_data if isinstance(binary_data, dict) else None


def load_selected_binary_file() -> BinaryFileChange | None:
    """Load the currently cached binary file."""
    if read_selected_change_kind() not in (
        SelectedChangeKind.BINARY,
        SelectedChangeKind.BATCH_BINARY,
    ):
        return None

    binary_data = read_selected_binary_data()
    if binary_data is None:
        return None

    try:
        return BinaryFileChange(
            old_path=binary_data["old_path"],
            new_path=binary_data["new_path"],
            change_type=binary_data["change_type"],
        )
    except KeyError:
        return None


def read_selected_gitlink_data() -> dict | None:
    """Read cached gitlink selection data, if structurally valid."""
    gitlink_path = get_selected_gitlink_file_json_path()
    if not gitlink_path.exists():
        return None
    try:
        gitlink_data = json.loads(read_text_file_contents(gitlink_path))
    except json.JSONDecodeError:
        return None
    return gitlink_data if isinstance(gitlink_data, dict) else None


def load_selected_gitlink_change() -> GitlinkChange | None:
    """Load the currently cached gitlink change."""
    if read_selected_change_kind() not in (
        SelectedChangeKind.GITLINK,
        SelectedChangeKind.BATCH_GITLINK,
    ):
        return None

    gitlink_data = read_selected_gitlink_data()
    if gitlink_data is None:
        return None

    try:
        return GitlinkChange(
            old_path=gitlink_data["old_path"],
            new_path=gitlink_data["new_path"],
            old_oid=gitlink_data.get("old_oid"),
            new_oid=gitlink_data.get("new_oid"),
            change_type=gitlink_data["change_type"],
        )
    except KeyError:
        return None


def read_selected_rename_data() -> dict | None:
    """Read cached rename selection data, if structurally valid."""
    rename_path = get_selected_rename_file_json_path()
    if not rename_path.exists():
        return None
    try:
        rename_data = json.loads(read_text_file_contents(rename_path))
    except json.JSONDecodeError:
        return None
    return rename_data if isinstance(rename_data, dict) else None


def load_selected_rename_change() -> RenameChange | None:
    """Load the currently cached rename change."""
    if read_selected_change_kind() != SelectedChangeKind.RENAME:
        return None

    rename_data = read_selected_rename_data()
    if rename_data is None:
        return None

    try:
        return RenameChange(
            old_path=rename_data["old_path"],
            new_path=rename_data["new_path"],
        )
    except KeyError:
        return None


def read_selected_text_deletion_data() -> dict | None:
    """Read cached text deletion selection data, if structurally valid."""
    deletion_path = get_selected_text_deletion_file_json_path()
    if not deletion_path.exists():
        return None
    try:
        deletion_data = json.loads(read_text_file_contents(deletion_path))
    except json.JSONDecodeError:
        return None
    return deletion_data if isinstance(deletion_data, dict) else None


def load_selected_text_deletion_change() -> TextFileDeletionChange | None:
    """Load the currently cached text file deletion change."""
    if read_selected_change_kind() != SelectedChangeKind.DELETION:
        return None

    deletion_data = read_selected_text_deletion_data()
    if deletion_data is None:
        return None

    try:
        return TextFileDeletionChange(
            old_path=deletion_data["old_path"],
            new_path=deletion_data.get("new_path", "/dev/null"),
        )
    except KeyError:
        return None


def cache_binary_file_change(
    binary_change: BinaryFileChange,
    *,
    kind: SelectedChangeKind = SelectedChangeKind.BINARY,
    batch_name: str | None = None,
    batch_binary_fingerprint: str | None = None,
) -> None:
    """Cache a binary file change as the current selected change."""
    if kind not in (SelectedChangeKind.BINARY, SelectedChangeKind.BATCH_BINARY):
        raise ValueError("binary selections must use a binary selected-change kind")
    file_path = binary_change.path()
    binary_data = {
        "old_path": binary_change.old_path,
        "new_path": binary_change.new_path,
        "change_type": binary_change.change_type,
        "batch_name": batch_name if kind == SelectedChangeKind.BATCH_BINARY else None,
        "batch_binary_fingerprint": (
            batch_binary_fingerprint
            if kind == SelectedChangeKind.BATCH_BINARY else
            None
        ),
    }
    _clear_selected_line_payload_files()
    write_text_file_contents(
        get_selected_binary_file_json_path(),
        json.dumps(binary_data, ensure_ascii=False, indent=0),
    )
    write_text_file_contents(
        get_selected_hunk_hash_file_path(),
        compute_binary_file_hash(binary_change),
    )
    if kind == SelectedChangeKind.BINARY:
        write_snapshots_for_selected_file_path(file_path)
    write_selected_change_kind(kind)


def cache_gitlink_change(
    gitlink_change: GitlinkChange,
    *,
    kind: SelectedChangeKind = SelectedChangeKind.GITLINK,
    batch_name: str | None = None,
    batch_gitlink_fingerprint: str | None = None,
) -> None:
    """Cache a gitlink change as the current selected change."""
    if kind not in (SelectedChangeKind.GITLINK, SelectedChangeKind.BATCH_GITLINK):
        raise ValueError("gitlink selections must use a gitlink selected-change kind")
    gitlink_data = {
        "old_path": gitlink_change.old_path,
        "new_path": gitlink_change.new_path,
        "old_oid": gitlink_change.old_oid,
        "new_oid": gitlink_change.new_oid,
        "change_type": gitlink_change.change_type,
        "batch_name": batch_name if kind == SelectedChangeKind.BATCH_GITLINK else None,
        "batch_gitlink_fingerprint": (
            batch_gitlink_fingerprint
            if kind == SelectedChangeKind.BATCH_GITLINK else
            None
        ),
    }
    _clear_selected_line_payload_files()
    write_text_file_contents(
        get_selected_gitlink_file_json_path(),
        json.dumps(gitlink_data, ensure_ascii=False, indent=0),
    )
    write_text_file_contents(
        get_selected_hunk_hash_file_path(),
        compute_gitlink_change_hash(gitlink_change),
    )
    write_selected_change_kind(kind)


def cache_rename_change(rename_change: RenameChange) -> None:
    """Cache a rename change as the current selected change."""
    rename_data = {
        "old_path": rename_change.old_path,
        "new_path": rename_change.new_path,
    }
    _clear_selected_line_payload_files()
    write_text_file_contents(
        get_selected_rename_file_json_path(),
        json.dumps(rename_data, ensure_ascii=False, indent=0),
    )
    write_text_file_contents(
        get_selected_hunk_hash_file_path(),
        compute_rename_change_hash(rename_change),
    )
    write_selected_change_kind(SelectedChangeKind.RENAME)


def cache_text_deletion_change(deletion_change: TextFileDeletionChange) -> None:
    """Cache a whole-text-file deletion as the current selected change."""
    deletion_data = {
        "old_path": deletion_change.old_path,
        "new_path": deletion_change.new_path,
    }
    _clear_selected_line_payload_files()
    write_text_file_contents(
        get_selected_text_deletion_file_json_path(),
        json.dumps(deletion_data, ensure_ascii=False, indent=0),
    )
    write_text_file_contents(
        get_selected_hunk_hash_file_path(),
        compute_text_file_deletion_hash(deletion_change),
    )
    write_snapshots_for_selected_file_path(deletion_change.path())
    write_selected_change_kind(SelectedChangeKind.DELETION)


def _clear_selected_line_payload_files() -> None:
    """Clear selected line/hunk state before storing an atomic file selection."""
    get_selected_hunk_patch_file_path().unlink(missing_ok=True)
    get_line_changes_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)
    get_processed_skip_ids_file_path().unlink(missing_ok=True)
