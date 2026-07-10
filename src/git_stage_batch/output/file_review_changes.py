"""ReviewChange construction for file review output models."""

from __future__ import annotations

from ..core.actionable_changes import ActionableSelection, ActionableSelectionReason
from ..core.line_selection import format_line_ids
from ..core.models import LineLevelChange
from ..i18n import _
from . import file_review_change_segments as _file_review_change_segments
from .file_review_model import ReviewChange


def _build_review_change(
    *,
    index: int,
    line_changes: LineLevelChange,
    segment: _file_review_change_segments.ReviewChangeSegment,
) -> ReviewChange:
    rows = segment.rows
    actionable = segment.actionable
    old_line_numbers = [
        line.old_line_number
        for line in rows
        if line.kind != "+" and line.old_line_number is not None
    ]
    new_line_numbers = [
        line.new_line_number
        for line in rows
        if line.kind != "-" and line.new_line_number is not None
    ]
    changed_kinds = {line.kind for line in rows if line.kind in ("+", "-")}
    reason = (
        ActionableSelectionReason.REPLACEMENT
        if {"-", "+"}.issubset(changed_kinds) else
        ActionableSelectionReason.SIMPLE
    )
    display_ids = actionable.display_ids if actionable is not None else tuple()
    selection_ids = (
        actionable.selection_ids
        if actionable is not None else
        tuple(
            line.id
            for line in rows
            if line.kind in ("+", "-") and line.id is not None
        )
    )
    selection_text = format_line_ids(list(display_ids)) if display_ids else None
    return ReviewChange(
        index=index,
        total=0,
        path=line_changes.path,
        hunk_header=line_changes.header,
        old_start=min(old_line_numbers) if old_line_numbers else None,
        old_end=max(old_line_numbers) if old_line_numbers else None,
        new_start=min(new_line_numbers) if new_line_numbers else None,
        new_end=max(new_line_numbers) if new_line_numbers else None,
        rows=rows,
        display_ids=display_ids,
        selection_ids=selection_ids,
        select_as=selection_text,
        reason=actionable.reason if actionable is not None else reason,
        is_oversized=False,
        note=(
            actionable.note
            if actionable is not None else
            _("not currently selectable")
        ),
        actions=actionable.actions if actionable is not None else tuple(),
    )


def build_file_review_changes(
    line_changes: LineLevelChange,
    actionable_selections: tuple[ActionableSelection, ...],
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[ReviewChange, ...]:
    """Build change records from line rows and prepared actionable selections."""
    segments = _file_review_change_segments.build_file_review_change_segments(
        line_changes,
        actionable_selections,
        display_id_by_selection_id,
    )
    changes = [
        _build_review_change(
            index=index,
            line_changes=line_changes,
            segment=segment,
        )
        for index, segment in enumerate(segments, start=1)
    ]

    total = len(changes)
    return tuple(
        ReviewChange(
            index=change.index,
            total=total,
            path=change.path,
            hunk_header=change.hunk_header,
            old_start=change.old_start,
            old_end=change.old_end,
            new_start=change.new_start,
            new_end=change.new_end,
            rows=change.rows,
            display_ids=change.display_ids,
            selection_ids=change.selection_ids,
            select_as=change.select_as,
            reason=change.reason,
            is_oversized=change.is_oversized,
            note=change.note,
            actions=change.actions,
        )
        for change in changes
    )
