"""Selected-change state file persistence."""

from __future__ import annotations

import json
import shutil
import tempfile

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ...core.diff_parser import build_line_changes_from_patch_lines
from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_text_file_deletion_hash,
)
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    LineLevelChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...editor import EditorBuffer, write_buffer_to_path
from ...exceptions import CommandError
from ...i18n import _
from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import (
    get_selected_change_clear_reason_file_path,
    get_selected_change_kind_file_path,
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
from ..line_state import convert_line_changes_to_serializable_dict
from .snapshots import write_snapshots_for_selected_file_path


class SelectedChangeKind(str, Enum):
    """Kinds of selected changes cached in session state."""

    HUNK = "hunk"
    FILE = "file"
    RENAME = "rename"
    DELETION = "deletion"
    BINARY = "binary"
    GITLINK = "submodule"
    BATCH_FILE = "batch-file"
    BATCH_BINARY = "batch-binary"
    BATCH_GITLINK = "batch-submodule"

class SelectedChangeClearReason(str, Enum):
    """Reasons selected change state was intentionally cleared."""

    AUTO_ADVANCE_DISABLED = "auto-advance-disabled"
    FILE_LIST = "file-list"
    STALE_BATCH_SELECTION = "stale-batch-selection"

class SelectedChangeStateSnapshot:
    """Temporary file copy of selected change state."""

    paths: dict[str, Path | None]
    temporary_directory: tempfile.TemporaryDirectory

    def close(self) -> None:
        self.temporary_directory.cleanup()

    def __enter__(self) -> SelectedChangeStateSnapshot:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

def write_selected_hunk_patch_lines(patch_lines: Sequence[bytes]) -> None:
    with EditorBuffer.from_chunks(iter(patch_lines)) as patch_buffer:
        write_buffer_to_path(get_selected_hunk_patch_file_path(), patch_buffer)

def write_line_changes_state(line_changes: LineLevelChange) -> None:
    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(
            convert_line_changes_to_serializable_dict(line_changes),
            ensure_ascii=False,
            indent=0,
        ),
    )

def load_line_changes_from_patch_path(patch_path: Path) -> LineLevelChange:
    with EditorBuffer.from_path(patch_path) as patch_lines:
        return build_line_changes_from_patch_lines(patch_lines)

def _selected_change_state_paths():
    """Return files that make up the cached selected change state."""
    return {
        "patch": get_selected_hunk_patch_file_path(),
        "hash": get_selected_hunk_hash_file_path(),
        "clear_reason": get_selected_change_clear_reason_file_path(),
        "kind": get_selected_change_kind_file_path(),
        "line_state": get_line_changes_json_file_path(),
        "rename": get_selected_rename_file_json_path(),
        "text_deletion": get_selected_text_deletion_file_json_path(),
        "binary": get_selected_binary_file_json_path(),
        "gitlink": get_selected_gitlink_file_json_path(),
        "index_snapshot": get_index_snapshot_file_path(),
        "working_snapshot": get_working_tree_snapshot_file_path(),
        "processed_include_ids": get_processed_include_ids_file_path(),
        "processed_skip_ids": get_processed_skip_ids_file_path(),
    }


def snapshot_selected_change_state() -> SelectedChangeStateSnapshot:
    """Capture the current selected change cache."""
    temporary_directory = tempfile.TemporaryDirectory()
    snapshot_root = Path(temporary_directory.name)
    snapshot_paths: dict[str, Path | None] = {}

    for name, path in _selected_change_state_paths().items():
        if not path.exists():
            snapshot_paths[name] = None
            continue

        snapshot_path = snapshot_root / name
        shutil.copyfile(path, snapshot_path)
        snapshot_paths[name] = snapshot_path

    return SelectedChangeStateSnapshot(
        paths=snapshot_paths,
        temporary_directory=temporary_directory,
    )

def restore_selected_change_state(snapshot: SelectedChangeStateSnapshot) -> None:
    """Restore a previously captured selected change cache."""
    for name, path in _selected_change_state_paths().items():
        snapshot_path = snapshot.paths.get(name)
        if snapshot_path is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot_path, path)

def clear_selected_change_persistence_files() -> None:
    """Clear cached selected change state files."""
    for path in _selected_change_state_paths().values():
        path.unlink(missing_ok=True)

def _clear_selected_line_payload_files() -> None:
    """Clear selected line/hunk state before storing an atomic file selection."""
    get_selected_hunk_patch_file_path().unlink(missing_ok=True)
    get_line_changes_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)
    get_processed_skip_ids_file_path().unlink(missing_ok=True)

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
    if read_selected_change_kind() not in (SelectedChangeKind.BINARY, SelectedChangeKind.BATCH_BINARY):
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
    if read_selected_change_kind() not in (SelectedChangeKind.GITLINK, SelectedChangeKind.BATCH_GITLINK):
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
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
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

def get_selected_change_file_path() -> str | None:
    """Return the file path for the currently cached selected change."""
    rename_change = load_selected_rename_change()
    if rename_change is not None:
        return rename_change.path()

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        return deletion_change.path()

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        return gitlink_change.path()

    binary_file = load_selected_binary_file()
    if binary_file is not None:
        return binary_file.new_path if binary_file.new_path != "/dev/null" else binary_file.old_path

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    line_changes = load_line_changes_from_patch_path(patch_path)
    return line_changes.path

def mark_selected_change_cleared_by_file_list(
    *,
    source: str,
    batch_name: str | None = None,
) -> None:
    """Record that a navigational file list intentionally cleared selection."""
    _write_selected_change_clear_reason(
        reason=SelectedChangeClearReason.FILE_LIST,
        source=source,
        batch_name=batch_name,
    )

def mark_selected_change_cleared_by_stale_batch_selection(
    *,
    batch_name: str,
    file_path: str,
) -> None:
    """Record that a batch mutation invalidated the selected batch file."""
    _write_selected_change_clear_reason(
        reason=SelectedChangeClearReason.STALE_BATCH_SELECTION,
        source="batch",
        batch_name=batch_name,
        file_path=file_path,
    )

def _write_selected_change_clear_reason(
    *,
    reason: SelectedChangeClearReason,
    source: str,
    batch_name: str | None = None,
    file_path: str | None = None,
) -> None:
    """Write a structured selected-change clear marker."""
    write_text_file_contents(
        get_selected_change_clear_reason_file_path(),
        json.dumps(
            {
                "reason": reason.value,
                "source": source,
                "batch_name": batch_name,
                "file_path": file_path,
            },
            ensure_ascii=False,
            indent=0,
        ),
    )

def _read_selected_change_clear_reason() -> dict[str, str | None] | None:
    """Return the structured clear marker, tolerating legacy plain-text state."""
    raw_reason = read_text_file_contents(get_selected_change_clear_reason_file_path()).strip()
    if not raw_reason:
        return None
    if raw_reason == SelectedChangeClearReason.FILE_LIST.value:
        return {
            "reason": SelectedChangeClearReason.FILE_LIST.value,
            "source": None,
            "batch_name": None,
        }
    try:
        data = json.loads(raw_reason)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    reason = data.get("reason")
    if reason not in {item.value for item in SelectedChangeClearReason}:
        return None
    return {
        "reason": reason,
        "source": data.get("source") if isinstance(data.get("source"), str) else None,
        "batch_name": data.get("batch_name") if isinstance(data.get("batch_name"), str) else None,
        "file_path": data.get("file_path") if isinstance(data.get("file_path"), str) else None,
    }

def selected_change_was_cleared_by_file_list(
    *,
    source: str | None = None,
    batch_name: str | None = None,
) -> bool:
    """Return whether the current empty selection came from a file list."""
    if read_selected_change_kind() is not None:
        return False
    marker = _read_selected_change_clear_reason()
    if marker is None:
        return False
    if marker["reason"] != SelectedChangeClearReason.FILE_LIST.value:
        return False
    marker_source = marker["source"]
    marker_batch_name = marker["batch_name"]
    if source is not None and marker_source != source:
        return False
    if batch_name is not None and marker_batch_name != batch_name:
        return False
    return True

def selected_change_was_cleared_by_stale_batch_selection(
    *,
    batch_name: str | None = None,
) -> bool:
    """Return whether the current empty selection is a stale batch selection."""
    if read_selected_change_kind() is not None:
        return False
    marker = _read_selected_change_clear_reason()
    if marker is None:
        return False
    if marker["reason"] != SelectedChangeClearReason.STALE_BATCH_SELECTION.value:
        return False
    if batch_name is not None and marker["batch_name"] != batch_name:
        return False
    return True

def refuse_bare_action_after_file_list(
    action_command: str,
    *,
    open_command: str = "git-stage-batch show --file PATH",
    source: str | None = None,
    batch_name: str | None = None,
) -> None:
    """Refuse a bare action after a navigational file list cleared selection."""
    if not selected_change_was_cleared_by_file_list(source=source, batch_name=batch_name):
        return
    raise CommandError(
        _(
            "No selected change.\n"
            "The last command only showed files; it did not choose one for follow-up actions.\n\n"
            "Run:\n"
            "  git-stage-batch show\n"
            "or choose a file with:\n"
            "  {open_command}\n"
            "before running:\n"
            "  git-stage-batch {action}"
        ).format(open_command=open_command, action=action_command)
    )

def refuse_bare_action_after_stale_batch_selection(
    action_command: str,
    *,
    batch_name: str,
) -> None:
    """Refuse a bare batch action after the selected batch file went stale."""
    if not selected_change_was_cleared_by_stale_batch_selection(batch_name=batch_name):
        return

    marker = _read_selected_change_clear_reason() or {}
    file_path = marker.get("file_path") or "the previously selected file"
    raise CommandError(
        _(
            "No selected change.\n"
            "The selected batch file '{file}' was changed or removed from batch '{batch}'.\n\n"
            "Open a current batch file with:\n"
            "  git-stage-batch show --from {batch} --file PATH\n"
            "before running:\n"
            "  git-stage-batch {action}"
        ).format(file=file_path, batch=batch_name, action=action_command)
    )

def write_selected_change_kind(kind: SelectedChangeKind) -> None:
    """Persist the kind of selected change cached in session state."""
    get_selected_change_clear_reason_file_path().unlink(missing_ok=True)
    if kind != SelectedChangeKind.RENAME:
        get_selected_rename_file_json_path().unlink(missing_ok=True)
    if kind != SelectedChangeKind.DELETION:
        get_selected_text_deletion_file_json_path().unlink(missing_ok=True)
    if kind not in (SelectedChangeKind.BINARY, SelectedChangeKind.BATCH_BINARY):
        get_selected_binary_file_json_path().unlink(missing_ok=True)
    if kind not in (SelectedChangeKind.GITLINK, SelectedChangeKind.BATCH_GITLINK):
        get_selected_gitlink_file_json_path().unlink(missing_ok=True)
    write_text_file_contents(get_selected_change_kind_file_path(), kind)


def read_selected_change_kind() -> SelectedChangeKind | None:
    """Return the kind of selected change cached in session state."""
    path = get_selected_change_kind_file_path()
    if not path.exists():
        return None

    raw_kind = read_text_file_contents(path).strip()
    if not raw_kind:
        return None

    try:
        return SelectedChangeKind(raw_kind)
    except ValueError:
        return None
