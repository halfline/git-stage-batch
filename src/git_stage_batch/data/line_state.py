"""Line-level state management for line operations."""

from __future__ import annotations

import json
from typing import Any, Optional

from ..core.models import CurrentLines, HunkHeader, LineEntry
from ..exceptions import exit_with_error
from ..i18n import _
from ..core.line_selection import read_line_ids_file
from ..utils.file_io import read_text_file_contents
from ..utils.paths import (
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
)


def convert_current_lines_to_serializable_dict(current_lines: CurrentLines) -> dict[str, Any]:
    """Convert CurrentLines to a JSON-serializable dictionary."""
    return {
        "path": current_lines.path,
        "header": {
            "old_start": current_lines.header.old_start,
            "old_len": current_lines.header.old_len,
            "new_start": current_lines.header.new_start,
            "new_len": current_lines.header.new_len,
        },
        "lines": [
            {
                "id": line_entry.id,
                "kind": line_entry.kind,
                "old_lineno": line_entry.old_line_number,
                "new_lineno": line_entry.new_line_number,
                "text": line_entry.text,
            }
            for line_entry in current_lines.lines
        ],
    }


def load_current_lines_from_state() -> Optional[CurrentLines]:
    """Load the current hunk from saved state.

    Returns:
        CurrentLines if state exists, None otherwise
    """
    if not get_current_hunk_patch_file_path().exists() or not get_current_lines_json_file_path().exists():
        return None
    data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
    header = HunkHeader(**data["header"])
    lines = [LineEntry(id=le["id"],
                       kind=le["kind"],
                       old_line_number=le["old_lineno"],
                       new_line_number=le["new_lineno"],
                       text=le["text"])
             for le in data["lines"]]
    return CurrentLines(path=data["path"], header=header, lines=lines)


def compute_remaining_changed_line_ids() -> list[int]:
    """Compute which changed line IDs haven't been processed yet."""
    current_lines = load_current_lines_from_state()
    if current_lines is None:
        exit_with_error(_("No current hunk. Run 'start' first."))
    all_changed_ids = set(current_lines.changed_line_ids())
    included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    processed_ids = included_ids | skipped_ids
    remaining_ids = all_changed_ids - processed_ids
    return sorted(remaining_ids)
