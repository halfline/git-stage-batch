"""Selected-change caching for file-scoped hunk views."""

from __future__ import annotations

import json
import subprocess
from typing import Optional

from ...core.hashing import compute_stable_hunk_hash_from_lines
from ...core.models import LineLevelChange
from ...utils.file_io import write_text_file_contents
from ...utils.paths import (
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_selected_hunk_hash_file_path,
)
from ..file_hunk_display import (
    render_file_as_single_hunk,
    render_unstaged_file_as_single_hunk,
)
from ..line_id_files import write_line_ids_file
from ..line_state import convert_line_changes_to_serializable_dict
from .snapshots import write_snapshots_for_selected_file_path
from .store import (
    SelectedChangeKind,
    write_selected_change_kind,
    write_selected_hunk_patch_lines,
)


def cache_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Cache all changes for a file as a single concatenated hunk.

    Reads the CURRENT working tree state for the file and fetches ALL
    hunks (ignoring blocklist/batches), concatenating them into one
    LineLevelChange with continuous line IDs.

    This always reflects the live working tree state, unlike regular
    hunk caching which uses snapshots.

    Args:
        file_path: Repository-relative path to file

    Returns:
        LineLevelChange with all file changes, or None if no changes
    """
    try:
        combined_line_changes = render_file_as_single_hunk(file_path)
        return _cache_combined_file_line_changes(file_path, combined_line_changes)
    except subprocess.CalledProcessError:
        return None


def cache_unstaged_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Cache the remaining unstaged changes for a file as a single hunk."""
    try:
        combined_line_changes = render_unstaged_file_as_single_hunk(file_path)
        return _cache_combined_file_line_changes(file_path, combined_line_changes)
    except subprocess.CalledProcessError:
        return None


def _cache_combined_file_line_changes(
    file_path: str,
    combined_line_changes: Optional[LineLevelChange],
) -> Optional[LineLevelChange]:
    """Persist a combined file-scoped view as the current selection."""
    if combined_line_changes is None:
        return None

    patch_lines = [
        f"--- a/{file_path}\n".encode("utf-8"),
        f"+++ b/{file_path}\n".encode("utf-8"),
        (
            f"@@ -{combined_line_changes.header.old_start},{combined_line_changes.header.old_len} "
            f"+{combined_line_changes.header.new_start},{combined_line_changes.header.new_len} @@\n"
        ).encode("utf-8"),
    ]
    for entry in combined_line_changes.lines:
        patch_lines.append(entry.kind.encode("utf-8") + entry.text_bytes + b"\n")
        if not entry.has_trailing_newline:
            patch_lines.append(b"\\ No newline at end of file\n")

    patch_hash = compute_stable_hunk_hash_from_lines(patch_lines)

    write_selected_hunk_patch_lines(patch_lines)
    write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
    write_selected_change_kind(SelectedChangeKind.FILE)
    write_line_ids_file(get_processed_include_ids_file_path(), set())
    write_line_ids_file(get_processed_skip_ids_file_path(), set())
    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(
            convert_line_changes_to_serializable_dict(combined_line_changes),
            ensure_ascii=False,
            indent=0,
        ),
    )

    write_snapshots_for_selected_file_path(file_path)

    return combined_line_changes
