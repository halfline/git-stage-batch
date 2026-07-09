"""Model construction for file review output."""

from __future__ import annotations

from ..core.actionable_changes import (
    ActionableSelection,
    ActionableSelectionReason,
    derive_actionable_selections,
)
from ..core.line_selection import format_line_ids
from ..core.models import LineEntry, LineLevelChange, ReviewActionGroup
from ..i18n import _
from . import file_review_layout
from .file_review_model import (
    FileReviewModel,
    FileReviewPage,
    ReviewChange,
    ReviewChangeFragment,
)


def _partly_selects_ownership_group(
    selection: ActionableSelection,
    ownership_group_sets: list[set[int]],
) -> bool:
    """Return whether a review selection splits a complete batch ownership group."""
    selected_ids = set(selection.selection_ids)
    return any(
        selected_ids & ownership_group
        and not ownership_group.issubset(selected_ids)
        for ownership_group in ownership_group_sets
    )


def _reason_for_selection_ids(
    line_changes: LineLevelChange,
    selection_ids: tuple[int, ...],
) -> ActionableSelectionReason:
    selected_id_set = set(selection_ids)
    saw_addition = False
    saw_deletion = False
    for line in line_changes.lines:
        if line.id not in selected_id_set or line.kind not in ("+", "-"):
            continue
        if line.kind == "+":
            saw_addition = True
        else:
            saw_deletion = True
        if saw_addition and saw_deletion:
            return ActionableSelectionReason.REPLACEMENT
    return ActionableSelectionReason.SIMPLE


def _actionable_selections_from_selection_groups(
    line_changes: LineLevelChange,
    selection_groups: tuple[tuple[int, ...], ...],
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[ActionableSelection, ...]:
    """Create review selections directly from complete ownership groups."""
    selections: list[ActionableSelection] = []
    for group in selection_groups:
        selection_ids = tuple(
            selection_id
            for selection_id in group
            if selection_id is not None
        )
        if not selection_ids:
            continue
        if display_id_by_selection_id is None:
            display_ids = selection_ids
        else:
            if any(
                selection_id not in display_id_by_selection_id
                for selection_id in selection_ids
            ):
                continue
            display_ids = tuple(
                display_id_by_selection_id[selection_id]
                for selection_id in selection_ids
            )
        selections.append(
            ActionableSelection(
                display_ids=display_ids,
                selection_ids=selection_ids,
                reason=_reason_for_selection_ids(line_changes, selection_ids),
            )
        )
    return tuple(selections)


def _display_actionable_selections_from_review_action_groups(
    line_changes: LineLevelChange,
    review_action_groups: tuple[ReviewActionGroup, ...],
    gutter_to_selection_id: dict[int, int] | None,
) -> tuple[ActionableSelection, ...]:
    """Derive user-facing batch review changes without splitting reset atoms."""
    action_group_by_selection_id = {
        selection_id: group
        for group in review_action_groups
        for selection_id in group.selection_ids
    }
    atomic_group_sets = [
        set(group.selection_ids)
        for group in review_action_groups
        if group.reason in (
            ActionableSelectionReason.REPLACEMENT.value,
            ActionableSelectionReason.STRUCTURAL_RUN.value,
        )
    ]
    selections = []
    for selection in derive_actionable_selections(
        line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
    ):
        chunk_display_ids: list[int] = []
        chunk_selection_ids: list[int] = []
        chunk_actions: tuple[str, ...] | None = None

        def flush_chunk() -> None:
            nonlocal chunk_display_ids, chunk_selection_ids, chunk_actions
            if not chunk_display_ids or chunk_actions is None:
                chunk_display_ids = []
                chunk_selection_ids = []
                chunk_actions = None
                return
            chunk = ActionableSelection(
                display_ids=tuple(chunk_display_ids),
                selection_ids=tuple(chunk_selection_ids),
                reason=_reason_for_selection_ids(
                    line_changes,
                    tuple(chunk_selection_ids),
                ),
                actions=chunk_actions,
            )
            if not _partly_selects_ownership_group(chunk, atomic_group_sets):
                selections.append(chunk)
            chunk_display_ids = []
            chunk_selection_ids = []
            chunk_actions = None

        for display_id, selection_id in zip(
            selection.display_ids,
            selection.selection_ids,
        ):
            action_group = action_group_by_selection_id.get(selection_id)
            if action_group is None:
                flush_chunk()
                continue
            actions = action_group.actions
            if chunk_actions is not None and actions != chunk_actions:
                flush_chunk()
            chunk_display_ids.append(display_id)
            chunk_selection_ids.append(selection_id)
            chunk_actions = actions
        flush_chunk()
    return tuple(selections)


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
    if review_action_groups is not None:
        actionable_selections = _display_actionable_selections_from_review_action_groups(
            line_changes,
            review_action_groups,
            gutter_to_selection_id,
        )
    elif actionable_selection_groups is not None:
        actionable_selections = _actionable_selections_from_selection_groups(
            line_changes,
            actionable_selection_groups,
            display_id_by_selection_id,
        )
        ownership_group_sets = [
            set(group)
            for group in actionable_selection_groups
            if group
        ]
        actionable_selections = tuple(
            selection
            for selection in actionable_selections
            if not _partly_selects_ownership_group(selection, ownership_group_sets)
        )
    else:
        actionable_selections = derive_actionable_selections(
            line_changes,
            gutter_to_selection_id=gutter_to_selection_id,
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

    body_budget = file_review_layout.body_budget()
    page_fragments: list[
        list[tuple[ReviewChange, tuple[LineEntry, ...], bool, bool]]
    ] = []
    current_page: list[tuple[ReviewChange, tuple[LineEntry, ...], bool, bool]] = []
    current_height = 0
    for change in changes:
        change_height = len(change.rows) + 2
        if change_height <= body_budget or body_budget <= 2:
            if current_page and current_height + change_height > body_budget:
                page_fragments.append(current_page)
                current_page = []
                current_height = 0
            current_page.append((change, change.rows, True, True))
            current_height += change_height
            continue

        rows_per_fragment = max(1, body_budget - 2)
        row_chunks = [
            tuple(change.rows[index:index + rows_per_fragment])
            for index in range(0, len(change.rows), rows_per_fragment)
        ]
        for chunk_index, row_chunk in enumerate(row_chunks):
            fragment_height = len(row_chunk) + 2
            if current_page:
                page_fragments.append(current_page)
                current_page = []
            current_page.append(
                (
                    change,
                    row_chunk,
                    chunk_index == 0,
                    chunk_index == len(row_chunks) - 1,
                )
            )
            current_height = fragment_height
            if chunk_index < len(row_chunks) - 1:
                page_fragments.append(current_page)
                current_page = []
                current_height = 0
    if current_page:
        page_fragments.append(current_page)
    if not page_fragments:
        page_fragments = [[]]

    change_pages: dict[int, list[int]] = {}
    for page_number, fragments in enumerate(page_fragments, start=1):
        for change, _rows, _is_first, _is_last in fragments:
            change_pages.setdefault(change.index, []).append(page_number)

    paged_changes: list[ReviewChange] = []
    for change in changes:
        pages_for_change = change_pages.get(change.index, [1])
        paged_changes.append(
            ReviewChange(
                index=change.index,
                total=change.total,
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
                is_oversized=(len(change.rows) + 2) > body_budget,
                note=change.note,
                actions=change.actions,
                first_page=min(pages_for_change),
                last_page=max(pages_for_change),
            )
        )
    by_index = {change.index: change for change in paged_changes}
    final_pages = tuple(
        FileReviewPage(
            page=page_number,
            changes=tuple(
                ReviewChangeFragment(
                    change=by_index[change.index],
                    rows=rows,
                    is_first_fragment=is_first,
                    is_last_fragment=is_last,
                )
                for change, rows, is_first, is_last in fragments
            ),
        )
        for page_number, fragments in enumerate(page_fragments, start=1)
    )
    return FileReviewModel(
        line_changes=line_changes,
        changes=tuple(paged_changes),
        pages=final_pages,
        display_id_by_selection_id=display_id_by_selection_id,
        review_action_groups=review_action_groups or (),
    )
