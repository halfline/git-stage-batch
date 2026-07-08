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
from ...core.models import (
    LineLevelChange,
)
from ...core.buffer import (
    LineBuffer,
    write_buffer_to_path,
)
from ...exceptions import CommandError
from ...i18n import _
from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import (
    get_index_snapshot_file_path,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_selected_binary_file_json_path,
    get_selected_change_clear_reason_file_path,
    get_selected_change_kind_file_path,
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


@dataclass
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
    with LineBuffer.from_chunks(iter(patch_lines)) as patch_buffer:
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


def cache_hunk_change(
    patch_lines: Sequence[bytes],
    hunk_hash: str,
    line_changes: LineLevelChange,
) -> None:
    """Cache a text hunk as the current selected change."""
    write_selected_hunk_patch_lines(patch_lines)
    write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)
    write_selected_change_kind(SelectedChangeKind.HUNK)
    write_line_changes_state(line_changes)
    write_snapshots_for_selected_file_path(line_changes.path)


def load_line_changes_from_patch_path(patch_path: Path) -> LineLevelChange:
    with LineBuffer.from_path(patch_path) as patch_lines:
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
    # processed_batch_ids is global state (union of all batches), not per-hunk state


def get_selected_change_file_path() -> str | None:
    """Return the file path for the currently cached selected change."""
    from . import file_changes as _selected_file_changes

    rename_change = _selected_file_changes.load_selected_rename_change()
    if rename_change is not None:
        return rename_change.path()

    deletion_change = _selected_file_changes.load_selected_text_deletion_change()
    if deletion_change is not None:
        return deletion_change.path()

    gitlink_change = _selected_file_changes.load_selected_gitlink_change()
    if gitlink_change is not None:
        return gitlink_change.path()

    binary_file = _selected_file_changes.load_selected_binary_file()
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


def mark_selected_change_cleared_by_auto_advance_disabled() -> None:
    """Record that an action left the next change unselected."""
    _write_selected_change_clear_reason(
        reason=SelectedChangeClearReason.AUTO_ADVANCE_DISABLED,
        source="auto-advance",
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


def selected_change_was_cleared_by_auto_advance_disabled() -> bool:
    """Return whether the current empty selection needs an explicit show."""
    if read_selected_change_kind() is not None:
        return False
    marker = _read_selected_change_clear_reason()
    if marker is None:
        return False
    return marker["reason"] == SelectedChangeClearReason.AUTO_ADVANCE_DISABLED.value


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


def refuse_bare_action_after_auto_advance_disabled(action_command: str) -> None:
    """Refuse a bare action after a command declined to select the next hunk."""
    if not selected_change_was_cleared_by_auto_advance_disabled():
        return
    raise CommandError(
        _(
            "No selected change.\n"
            "The previous command did not choose the next hunk because automatic "
            "advancement is disabled.\n\n"
            "Run:\n"
            "  git-stage-batch show\n"
            "before running:\n"
            "  git-stage-batch {action}"
        ).format(action=action_command)
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
