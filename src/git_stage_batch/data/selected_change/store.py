"""Selected-change state file persistence."""

from __future__ import annotations

import json

from collections.abc import Sequence
from enum import Enum
from pathlib import Path

from ...core.diff_parser import build_line_changes_from_patch_lines
from ...core.models import LineLevelChange
from ...editor import EditorBuffer, write_buffer_to_path
from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import (
    get_selected_change_clear_reason_file_path,
    get_selected_change_kind_file_path,
    get_line_changes_json_file_path,
    get_selected_hunk_patch_file_path,
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
