"""Display ID helpers for file review output."""

from __future__ import annotations

from ..core.models import LineEntry


def display_ids_for_rows(
    rows: tuple[LineEntry, ...],
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[int, ...]:
    """Return unique review display IDs for renderable rows."""
    display_ids: list[int] = []
    seen: set[int] = set()
    for row in rows:
        if row.id is None:
            continue
        display_id = (
            row.id
            if display_id_by_selection_id is None else
            display_id_by_selection_id.get(row.id)
        )
        if display_id is None or display_id in seen:
            continue
        display_ids.append(display_id)
        seen.add(display_id)
    return tuple(display_ids)
