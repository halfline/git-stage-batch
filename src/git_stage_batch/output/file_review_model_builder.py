"""Model construction for file review output."""

from __future__ import annotations

from ..core.actionable_changes import ActionableSelection, ActionableSelectionReason
from ..core.line_selection import format_line_ids
from ..core.models import LineEntry, LineLevelChange, ReviewActionGroup
from ..i18n import _
from .file_review_model import FileReviewModel, ReviewChange
from .file_review_model_selections import derive_file_review_actionable_selections
from .file_review_pagination import paginate_file_review_changes


def build_file_review_model(
    line_changes: LineLevelChange,
    *,
    gutter_to_selection_id: dict[int, int] | None = None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None = None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None = None,
) -> FileReviewModel:
    """Build a conservative change-first page model from a file-scoped hunk."""
    changes: list[ReviewChange] = []
    current_rows: list[LineEntry] = []
    display_id_by_selection_id = (
        {
            selection_id: gutter_id
            for gutter_id, selection_id in gutter_to_selection_id.items()
        }
        if gutter_to_selection_id is not None else None
    )
    actionable_selections = derive_file_review_actionable_selections(
        line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
        actionable_selection_groups=actionable_selection_groups,
        review_action_groups=review_action_groups,
        display_id_by_selection_id=display_id_by_selection_id,
    )
    actionable_by_selection = {
        selection.selection_ids: selection
        for selection in actionable_selections
    }

    def append_change(rows: list[LineEntry], actionable: ActionableSelection | None) -> None:
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
            if {"-", "+"}.issubset(changed_kinds)
            else ActionableSelectionReason.SIMPLE
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
        changes.append(
            ReviewChange(
                index=len(changes) + 1,
                total=0,
                path=line_changes.path,
                hunk_header=line_changes.header,
                old_start=min(old_line_numbers) if old_line_numbers else None,
                old_end=max(old_line_numbers) if old_line_numbers else None,
                new_start=min(new_line_numbers) if new_line_numbers else None,
                new_end=max(new_line_numbers) if new_line_numbers else None,
                rows=tuple(rows),
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
                append_change(active_rows, active_actionable)
            active_rows = []
            active_actionable = None
            has_active_change = False

        def activate_changed_run(*, trailing_rows: list[LineEntry] | None = None) -> None:
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

    total = len(changes)
    changes = [
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
    ]

    paged_changes, pages = paginate_file_review_changes(
        tuple(changes),
    )
    return FileReviewModel(
        line_changes=line_changes,
        changes=paged_changes,
        pages=pages,
        display_id_by_selection_id=display_id_by_selection_id,
        review_action_groups=review_action_groups or (),
    )
