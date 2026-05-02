"""Session-persistent consumed-selection ownership for hidden masking."""

from __future__ import annotations

import json
from typing import Any

from ..batch.ownership import (
    BatchOwnership,
    advance_batch_source_for_file_with_provenance,
    detect_stale_batch_source_for_selection,
    merge_batch_ownership,
    translate_lines_to_batch_ownership,
)
from ..batch.source_refresh import (
    _refresh_selected_lines_against_new_source,
    _refresh_selected_lines_against_source_content,
)
from .batch_sources import create_batch_source_commit
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_session_consumed_selections_file_path


def load_consumed_selections_metadata() -> dict[str, Any]:
    """Load hidden consumed-selection metadata."""
    path = get_session_consumed_selections_file_path()
    if not path.exists():
        return {"files": {}}

    try:
        data = json.loads(read_text_file_contents(path))
    except json.JSONDecodeError:
        return {"files": {}}

    files = data.get("files", {})
    if not isinstance(files, dict):
        return {"files": {}}
    return {"files": files}


def read_consumed_file_metadata(file_path: str) -> dict[str, Any] | None:
    """Return hidden consumed-selection metadata for one file."""
    metadata = load_consumed_selections_metadata()
    file_metadata = metadata.get("files", {}).get(file_path)
    return file_metadata if isinstance(file_metadata, dict) else None


def record_consumed_selection(
    file_path: str,
    *,
    source_content: bytes,
    selected_lines: list,
    replacement_mask: dict[str, list[str]] | None = None,
) -> None:
    """Persist consumed selection ownership for masking across `again`."""
    metadata = load_consumed_selections_metadata()
    files = metadata.setdefault("files", {})
    existing_file_metadata = read_consumed_file_metadata(file_path)

    if existing_file_metadata is not None:
        existing_ownership = BatchOwnership.from_metadata_dict(existing_file_metadata)
        batch_source_commit = existing_file_metadata["batch_source_commit"]
        if detect_stale_batch_source_for_selection(selected_lines):
            advance_result = advance_batch_source_for_file_with_provenance(
                batch_name="consumed-selections",
                file_path=file_path,
                old_batch_source_commit=batch_source_commit,
                existing_ownership=existing_ownership,
            )
            batch_source_commit = advance_result.batch_source_commit
            existing_ownership = advance_result.ownership
            selected_lines = _refresh_selected_lines_against_source_content(
                selected_lines,
                source_content=advance_result.source_content,
                working_content=advance_result.working_content,
                working_line_map=advance_result.working_line_map,
            )
        new_ownership = translate_lines_to_batch_ownership(selected_lines)
        merged_ownership = merge_batch_ownership(existing_ownership, new_ownership)
    else:
        if detect_stale_batch_source_for_selection(selected_lines):
            selected_lines = _refresh_selected_lines_against_new_source(selected_lines)
        merged_ownership = translate_lines_to_batch_ownership(selected_lines)
        batch_source_commit = create_batch_source_commit(
            file_path,
            file_content_override=source_content,
        )

    files[file_path] = {
        "batch_source_commit": batch_source_commit,
        **merged_ownership.to_metadata_dict(),
    }
    existing_replacement_masks = existing_file_metadata.get("replacement_masks", []) if existing_file_metadata else []
    if replacement_mask is not None:
        replacement_masks = existing_replacement_masks[:]
        replacement_masks.append(replacement_mask)
        files[file_path]["replacement_masks"] = replacement_masks
    elif existing_replacement_masks:
        files[file_path]["replacement_masks"] = existing_replacement_masks
    write_text_file_contents(
        get_session_consumed_selections_file_path(),
        json.dumps(metadata, ensure_ascii=False, indent=2),
    )
