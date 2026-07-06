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
from ...core.models import LineLevelChange
from ...editor import EditorBuffer, write_buffer_to_path
from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import (
    get_selected_change_clear_reason_file_path,
    get_selected_change_kind_file_path,
    get_index_snapshot_file_path,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_working_tree_snapshot_file_path,
)
from ..line_state import convert_line_changes_to_serializable_dict


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

def write_selected_change_kind(kind: SelectedChangeKind) -> None:
    """Persist the kind of selected change cached in session state."""
    get_selected_change_clear_reason_file_path().unlink(missing_ok=True)
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
