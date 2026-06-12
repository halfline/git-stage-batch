"""Best-effort helpers for preserving user-facing line IDs."""

from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher

from .models import LineEntry, LineLevelChange


def _changed_line_entries(
    line_changes: LineLevelChange,
) -> list[tuple[int, LineEntry]]:
    return [
        (index, line)
        for index, line in enumerate(line_changes.lines)
        if line.kind in ("+", "-") and line.id is not None
    ]


def _line_signature(line: LineEntry) -> tuple[str, bytes, bool]:
    return (line.kind, line.text_bytes, line.has_trailing_newline)


def preserve_line_ids_from_previous_view(
    previous: LineLevelChange | None,
    current: LineLevelChange,
) -> LineLevelChange:
    """Carry unchanged line IDs from a previous view into a refreshed view.

    This is intentionally best-effort. It only preserves IDs for equal changed
    line runs that can be aligned by kind/content/order. New or unmatched rows
    receive IDs after the previous maximum so old holes stay empty.
    """
    if previous is None or previous.path != current.path:
        return current

    previous_entries = _changed_line_entries(previous)
    current_entries = _changed_line_entries(current)
    if not previous_entries or not current_entries:
        return current

    previous_signatures = [_line_signature(line) for _index, line in previous_entries]
    current_signatures = [_line_signature(line) for _index, line in current_entries]
    matcher = SequenceMatcher(
        None,
        previous_signatures,
        current_signatures,
        autojunk=False,
    )

    id_by_current_index: dict[int, int] = {}
    used_ids: set[int] = set()
    for tag, previous_start, previous_end, current_start, current_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        length = min(previous_end - previous_start, current_end - current_start)
        for offset in range(length):
            previous_line = previous_entries[previous_start + offset][1]
            current_index = current_entries[current_start + offset][0]
            if previous_line.id is None or previous_line.id in used_ids:
                continue
            id_by_current_index[current_index] = previous_line.id
            used_ids.add(previous_line.id)

    previous_ids = [
        line.id
        for _index, line in previous_entries
        if line.id is not None
    ]
    next_id = max(previous_ids, default=0) + 1

    changed = False
    remapped_lines: list[LineEntry] = []
    for index, line in enumerate(current.lines):
        if line.kind not in ("+", "-") or line.id is None:
            remapped_lines.append(line)
            continue

        replacement_id = id_by_current_index.get(index)
        if replacement_id is None:
            while next_id in used_ids:
                next_id += 1
            replacement_id = next_id
            used_ids.add(replacement_id)
            next_id += 1

        if line.id == replacement_id:
            remapped_lines.append(line)
        else:
            remapped_lines.append(replace(line, id=replacement_id))
            changed = True

    if not changed:
        return current
    return replace(current, lines=remapped_lines)
