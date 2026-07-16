"""Consumed replacement-mask filtering for selected hunk views."""

from __future__ import annotations

from ..core.models import LineEntry, LineLevelChange
from .consumed_selections import read_consumed_file_metadata


def filter_consumed_replacement_masks(
    line_changes: LineLevelChange,
) -> LineLevelChange | None:
    """Hide synthetic replacement runs created by `include --line --as`."""
    file_metadata = read_consumed_file_metadata(line_changes.path)
    return filter_consumed_replacement_masks_with_metadata(
        line_changes,
        file_metadata=file_metadata,
    )


def filter_consumed_replacement_masks_with_metadata(
    line_changes: LineLevelChange,
    *,
    file_metadata: dict | None,
) -> LineLevelChange | None:
    """Hide replacement runs using caller-supplied consumed metadata."""
    replacement_masks = (
        file_metadata.get("replacement_masks", []) if file_metadata else []
    )
    if not replacement_masks:
        return line_changes

    normalized_masks: set[tuple[tuple[str, str], ...]] = set()
    for mask in replacement_masks:
        deleted_signature = tuple(("-", text) for text in mask.get("deleted_lines", []))
        added_signature = tuple(("+", text) for text in mask.get("added_lines", []))
        full_signature = deleted_signature + added_signature
        if full_signature:
            normalized_masks.add(full_signature)
        if deleted_signature:
            normalized_masks.add(deleted_signature)
        if added_signature:
            normalized_masks.add(added_signature)

    filtered_lines = []
    changed_run: list[LineEntry] = []

    def flush_changed_run() -> None:
        nonlocal changed_run
        if not changed_run:
            return
        run_signature = tuple(
            (line.kind, line.display_text())
            for line in changed_run
            if line.kind in ("+", "-")
        )
        if run_signature not in normalized_masks:
            filtered_lines.extend(changed_run)
        changed_run = []

    for line_entry in line_changes.lines:
        if line_entry.kind in ("+", "-"):
            changed_run.append(line_entry)
            continue
        flush_changed_run()
        filtered_lines.append(line_entry)

    flush_changed_run()

    has_changes_after_filter = any(line.kind in ("+", "-") for line in filtered_lines)
    if not has_changes_after_filter:
        return None

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=filtered_lines,
    )
