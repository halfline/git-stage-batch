"""Batch file display selected-state caching."""

from __future__ import annotations

import json
from typing import Optional

from ..batch.query import read_batch_metadata
from ..core.hashing import compute_stable_hunk_hash_from_lines
from ..core.models import RenderedBatchDisplay
from ..exceptions import CommandError
from ..utils.file_io import write_text_file_contents
from ..utils.paths import (
    get_index_snapshot_file_path,
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
    get_working_tree_snapshot_file_path,
)
from ..batch.file_display import render_batch_file_display
from .line_state import convert_line_changes_to_serializable_dict
from .selected_change.store import (
    SelectedChangeKind,
    write_selected_change_kind,
    write_selected_hunk_patch_lines,
)


def cache_batch_as_single_hunk(
    batch_name: str,
    file_path: str | None = None,
    metadata: dict | None = None,
) -> Optional[RenderedBatchDisplay]:
    """Load one batch file and cache it as the selected hunk."""
    if metadata is None:
        metadata = read_batch_metadata(batch_name)
    files = metadata.get("files", {})

    if not files:
        return None

    if file_path is None:
        file_path = sorted(files.keys())[0]
    elif file_path not in files:
        raise CommandError(f"File '{file_path}' not found in batch '{batch_name}'")

    rendered = render_batch_file_display(batch_name, file_path, metadata=metadata)
    if rendered is None:
        return None

    cache_rendered_batch_file_display(file_path, rendered)
    return rendered


def cache_rendered_batch_file_display(
    file_path: str,
    rendered: RenderedBatchDisplay,
) -> None:
    """Cache an already rendered batch file as the selected hunk."""
    line_changes = rendered.line_changes
    line_entries = line_changes.lines
    header = line_changes.header

    addition_count = sum(1 for entry in line_entries if entry.kind == "+")
    deletion_count = sum(1 for entry in line_entries if entry.kind == "-")

    old_path = (
        "/dev/null"
        if deletion_count == 0 and addition_count > 0
        else f"a/{file_path}"
    )
    new_path = (
        "/dev/null"
        if addition_count == 0 and deletion_count > 0
        else f"b/{file_path}"
    )

    patch_lines = [
        f"--- {old_path}\n".encode("utf-8"),
        f"+++ {new_path}\n".encode("utf-8"),
        (
            f"@@ -{header.old_start},{header.old_len} "
            f"+{header.new_start},{header.new_len} @@\n"
        ).encode("utf-8"),
    ]
    for entry in line_entries:
        patch_lines.append(entry.kind.encode("utf-8") + entry.text_bytes + b"\n")
        if not entry.has_trailing_newline:
            patch_lines.append(b"\\ No newline at end of file\n")

    patch_hash = compute_stable_hunk_hash_from_lines(patch_lines)

    write_selected_hunk_patch_lines(patch_lines)
    write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
    write_selected_change_kind(SelectedChangeKind.BATCH_FILE)

    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(
            convert_line_changes_to_serializable_dict(line_changes),
            ensure_ascii=False,
            indent=0,
        ),
    )

    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
