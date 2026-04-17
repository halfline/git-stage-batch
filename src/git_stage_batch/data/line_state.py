"""Line-level state management for line operations."""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from typing import Any, Optional

from ..core.models import LineLevelChange, HunkHeader, LineEntry
from ..exceptions import exit_with_error
from ..i18n import _
from ..core.line_selection import read_line_ids_file
from ..utils.file_io import read_text_file_contents
from ..utils.paths import (
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
)


def convert_line_changes_to_serializable_dict(line_changes: LineLevelChange) -> dict[str, Any]:
    """Convert LineLevelChange to a JSON-serializable dictionary."""
    return {
        "path": line_changes.path,
        "header": {
            "old_start": line_changes.header.old_start,
            "old_len": line_changes.header.old_len,
            "new_start": line_changes.header.new_start,
            "new_len": line_changes.header.new_len,
        },
        "lines": [
            {
                "id": line_entry.id,
                "kind": line_entry.kind,
                "old_lineno": line_entry.old_line_number,
                "new_lineno": line_entry.new_line_number,
                "text_bytes_b64": b64encode(line_entry.text_bytes).decode("ascii"),
                "text": line_entry.text,
                "source_line": line_entry.source_line,
            }
            for line_entry in line_changes.lines
        ],
    }


def load_line_changes_from_state() -> Optional[LineLevelChange]:
    """Load the selected hunk from saved state.

    Returns:
        LineLevelChange if state exists, None otherwise
    """
    if not get_selected_hunk_patch_file_path().exists() or not get_line_changes_json_file_path().exists():
        return None
    data = json.loads(read_text_file_contents(get_line_changes_json_file_path()))
    header = HunkHeader(**data["header"])

    def line_text_bytes(line_entry_data: dict[str, Any]) -> bytes:
        if "text_bytes_b64" in line_entry_data:
            return b64decode(line_entry_data["text_bytes_b64"].encode("ascii"))
        return line_entry_data["text"].encode("utf-8", errors="surrogateescape")

    lines = []
    for le in data["lines"]:
        text_bytes = line_text_bytes(le)
        lines.append(LineEntry(id=le["id"],
                               kind=le["kind"],
                               old_line_number=le["old_lineno"],
                               new_line_number=le["new_lineno"],
                               text_bytes=text_bytes,
                               text=text_bytes.decode("utf-8", errors="replace"),
                               source_line=le.get("source_line")))
    return LineLevelChange(path=data["path"], header=header, lines=lines)


def compute_remaining_changed_line_ids() -> list[int]:
    """Compute which changed line IDs haven't been processed yet."""
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        exit_with_error(_("No selected hunk. Run 'start' first."))
    all_changed_ids = set(line_changes.changed_line_ids())
    included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    processed_ids = included_ids | skipped_ids
    remaining_ids = all_changed_ids - processed_ids
    return sorted(remaining_ids)
