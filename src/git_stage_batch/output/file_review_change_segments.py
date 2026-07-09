"""Segment file-review rows into change groups."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.actionable_changes import ActionableSelection
from ..core.models import LineEntry, LineLevelChange


@dataclass(frozen=True)
class ReviewChangeSegment:
    rows: tuple[LineEntry, ...]
    actionable: ActionableSelection | None


def build_file_review_change_segments(
    line_changes: LineLevelChange,
    actionable_selections: tuple[ActionableSelection, ...],
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[ReviewChangeSegment, ...]:
    """Group file-review rows before ReviewChange record assembly."""
    segments: list[ReviewChangeSegment] = []
    current_rows: list[LineEntry] = []
    actionable_by_selection = {
        selection.selection_ids: selection
        for selection in actionable_selections
    }

    def append_segment(
        rows: list[LineEntry],
        actionable: ActionableSelection | None,
    ) -> None:
        segments.append(
            ReviewChangeSegment(
                rows=tuple(rows),
                actionable=actionable,
            )
        )

    def flush_segment() -> None:
        nonlocal current_rows
        pending_rows: list[LineEntry] = []
        active_rows: list[LineEntry] = []
        active_actionable: ActionableSelection | None = None
        has_active_change = False
        changed_run: list[LineEntry] = []
        changed_run_displayable: bool | None = None
        actionable_group_by_selection_id = {
            selection_id: selection.selection_ids
            for selection in actionable_selections
            for selection_id in selection.selection_ids
        }

        def flush_active_change() -> None:
            nonlocal active_rows, active_actionable, has_active_change
            if has_active_change and active_rows:
                append_segment(active_rows, active_actionable)
            active_rows = []
            active_actionable = None
            has_active_change = False

        def activate_changed_run(
            *,
            trailing_rows: list[LineEntry] | None = None,
        ) -> None:
            nonlocal pending_rows, changed_run, active_rows, active_actionable
            nonlocal has_active_change, changed_run_displayable

            def actionable_for_chunk(
                chunk_rows: list[LineEntry],
                group: tuple[int, ...] | None,
            ) -> ActionableSelection | None:
                if group is None:
                    return None
                chunk_selection_ids = tuple(
                    line.id
                    for line in chunk_rows
                    if line.id is not None
                )
                if chunk_selection_ids != group:
                    return None
                return actionable_by_selection.get(group)

            chunks: list[tuple[list[LineEntry], ActionableSelection | None]] = []
            current_chunk: list[LineEntry] = []
            current_group: tuple[int, ...] | None = None
            for line in changed_run:
                group = (
                    actionable_group_by_selection_id.get(line.id)
                    if changed_run_displayable else
                    None
                )
                if current_chunk and group != current_group:
                    chunks.append(
                        (
                            current_chunk,
                            actionable_for_chunk(current_chunk, current_group),
                        )
                    )
                    current_chunk = []
                current_chunk.append(line)
                current_group = group

            if current_chunk:
                chunks.append(
                    (
                        current_chunk,
                        actionable_for_chunk(current_chunk, current_group),
                    )
                )

            for index, (chunk_rows, actionable) in enumerate(chunks):
                flush_active_change()
                active_rows = (pending_rows if index == 0 else []) + chunk_rows
                if trailing_rows is not None and index == len(chunks) - 1:
                    active_rows.extend(trailing_rows)
                active_actionable = actionable
                has_active_change = True
            pending_rows = []
            changed_run = []
            changed_run_displayable = None

        def changed_row_displayable(row: LineEntry) -> bool | None:
            if row.kind not in ("+", "-") or row.id is None:
                return None
            if display_id_by_selection_id is None:
                return True
            return row.id in display_id_by_selection_id

        for row in current_rows:
            row_displayable = changed_row_displayable(row)
            if row_displayable is not None:
                if changed_run and changed_run_displayable != row_displayable:
                    activate_changed_run()
                changed_run.append(row)
                changed_run_displayable = row_displayable
                continue
            if changed_run:
                activate_changed_run(trailing_rows=[row])
                continue
            if has_active_change:
                active_rows.append(row)
            else:
                pending_rows.append(row)

        if changed_run:
            activate_changed_run()
        flush_active_change()
        current_rows = []

    for line in line_changes.lines:
        is_gap = (
            line.id is None
            and line.kind == " "
            and line.old_line_number is None
            and line.new_line_number is None
            and line.source_line is None
        )
        if is_gap:
            flush_segment()
            current_rows.append(line)
            continue
        current_rows.append(line)
    flush_segment()

    return tuple(segments)
