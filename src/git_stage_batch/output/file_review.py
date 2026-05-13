"""Page-aware file review rendering."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass

from ..core.actionable_changes import (
    ActionableSelection,
    ActionableSelectionReason,
    derive_actionable_selections,
)
from ..core.line_selection import format_line_ids, parse_positive_selection
from ..core.models import HunkHeader, LineEntry, LineLevelChange, ReviewActionGroup
from ..data.file_review_state import (
    FileReviewAction,
    FileReviewState,
    FileReviewSelectionState,
    ReviewSource,
    compute_current_file_review_diff_fingerprint,
    fingerprint_selected_file_view,
    _line_action_command,
)
from ..data.hunk_tracking import SelectedChangeKind
from ..exceptions import CommandError
from ..i18n import _
from .colors import Colors

DEFAULT_NON_TTY_REVIEW_LINES = 80
DEFAULT_REVIEW_WIDTH = 80
REVIEW_HEADER_LINES = 3
REVIEW_FOOTER_LINES = 9
PAGER_EXIT_MARGIN_LINES = 1
MINIMUM_BODY_LINES = 8


def _quote(value: str) -> str:
    return shlex.quote(value)


@dataclass(frozen=True)
class ReviewChange:
    """A complete actionable change group in a file review."""

    index: int
    total: int
    path: str
    hunk_header: HunkHeader
    old_start: int | None
    old_end: int | None
    new_start: int | None
    new_end: int | None
    rows: tuple[LineEntry, ...]
    display_ids: tuple[int, ...]
    selection_ids: tuple[int, ...]
    select_as: str | None
    reason: ActionableSelectionReason
    is_oversized: bool
    note: str | None
    actions: tuple[str, ...] = ()
    first_page: int = 1
    last_page: int = 1


@dataclass(frozen=True)
class ReviewChangeFragment:
    """A rendered fragment of a review change on one page."""

    change: ReviewChange
    rows: tuple[LineEntry, ...]
    is_first_fragment: bool
    is_last_fragment: bool


@dataclass(frozen=True)
class FileReviewPage:
    """One semantic review page."""

    page: int
    changes: tuple[ReviewChangeFragment, ...]


@dataclass(frozen=True)
class FileReviewModel:
    """A paginated file review model."""

    line_changes: LineLevelChange
    changes: tuple[ReviewChange, ...]
    pages: tuple[FileReviewPage, ...]
    display_id_by_selection_id: dict[int, int] | None = None
    review_action_groups: tuple[ReviewActionGroup, ...] = ()


@dataclass(frozen=True)
class FileReviewView:
    """Selected pages from a file review model."""

    source: ReviewSource
    path: str
    page_spec: str
    shown_pages: tuple[int, ...]
    page_count: int
    pages: tuple[FileReviewPage, ...]
    complete_changes: tuple[ReviewChange, ...]
    partial_changes: tuple[ReviewChange, ...]
    entire_file_shown: bool


def parse_page_selection(page_spec: str, page_count: int, file_path: str) -> tuple[int, ...]:
    """Parse and validate a page selection after page count is known."""
    normalized_spec = page_spec.strip().lower()
    if normalized_spec == "all":
        return tuple(range(1, page_count + 1))

    tokens = [token.strip() for token in normalized_spec.split(",")]
    if any(token == "" for token in tokens):
        raise CommandError(_("Page selection contains an empty item."))
    if any(token == "all" for token in tokens):
        raise CommandError(_("`all` cannot be combined with other page selections."))

    try:
        pages = parse_positive_selection(
            normalized_spec,
            item_name=_("Page"),
            reject_empty_items=True,
        )
    except ValueError as error:
        raise CommandError(
            _("Invalid page selection '{spec}': {error}").format(
                spec=page_spec,
                error=error,
            )
        ) from error

    if not pages:
        raise CommandError(_("Page selection cannot be empty."))
    highest_page = max(pages)
    if highest_page > page_count:
        raise CommandError(
            _("Page {page} is outside the file review for {file}.\n"
              "Available pages: 1-{page_count}.").format(
                page=highest_page,
                file=file_path,
                page_count=page_count,
            )
        )
    return tuple(sorted(set(pages)))


def normalize_page_spec(shown_pages: tuple[int, ...], page_count: int) -> str:
    """Return a compact persisted page specification."""
    if set(shown_pages) == set(range(1, page_count + 1)):
        return "all"
    return format_line_ids(list(shown_pages))


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


def _coerce_actionable_reason(reason: str) -> ActionableSelectionReason:
    try:
        return ActionableSelectionReason(reason)
    except ValueError:
        return ActionableSelectionReason.SIMPLE


def _reason_for_selection_ids(
    line_changes: LineLevelChange,
    selection_ids: tuple[int, ...],
) -> ActionableSelectionReason:
    selected_id_set = set(selection_ids)
    changed_kinds = {
        line.kind
        for line in line_changes.lines
        if line.id in selected_id_set
        and line.kind in ("+", "-")
    }
    return (
        ActionableSelectionReason.REPLACEMENT
        if {"-", "+"}.issubset(changed_kinds)
        else ActionableSelectionReason.SIMPLE
    )


def _actionable_selections_from_selection_groups(
    line_changes: LineLevelChange,
    selection_groups: tuple[tuple[int, ...], ...],
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[ActionableSelection, ...]:
    """Create review selections directly from complete ownership groups."""
    selections: list[ActionableSelection] = []
    for group in selection_groups:
        selection_ids = tuple(selection_id for selection_id in group if selection_id is not None)
        if not selection_ids:
            continue
        if display_id_by_selection_id is None:
            display_ids = selection_ids
        else:
            if any(selection_id not in display_id_by_selection_id for selection_id in selection_ids):
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

        for display_id, selection_id in zip(selection.display_ids, selection.selection_ids):
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
            tuple(line.id for line in rows if line.kind in ("+", "-") and line.id is not None)
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
                note=actionable.note if actionable is not None else _("not currently selectable"),
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
            nonlocal pending_rows, changed_run, active_rows, active_actionable, has_active_change, changed_run_displayable
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

    body_budget = _body_budget()
    page_fragments: list[list[tuple[ReviewChange, tuple[LineEntry, ...], bool, bool]]] = []
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


def _body_budget() -> int:
    size = _review_terminal_size()
    estimated_footer_lines = _estimate_file_review_footer_height()
    reserved_lines = REVIEW_HEADER_LINES + estimated_footer_lines + PAGER_EXIT_MARGIN_LINES
    return max(MINIMUM_BODY_LINES, size.lines - reserved_lines)


def _review_terminal_size():
    return shutil.get_terminal_size(fallback=(DEFAULT_REVIEW_WIDTH, DEFAULT_NON_TTY_REVIEW_LINES))


def _estimate_file_review_footer_height(_complete_change_count: int = 3) -> int:
    return REVIEW_FOOTER_LINES


def resolve_default_review_pages(
    model: FileReviewModel,
    *,
    requested_page_spec: str | None,
    previous_selection: LineLevelChange | None = None,
) -> tuple[int, ...]:
    """Resolve explicit pages, selected-hunk anchor, or default page 1."""
    page_count = len(model.pages)
    if requested_page_spec is not None:
        return parse_page_selection(requested_page_spec, page_count, model.line_changes.path)
    if page_count <= 1:
        return (1,)
    if previous_selection is not None and previous_selection.path == model.line_changes.path:
        for change in model.changes:
            if _change_overlaps_line_change(change, previous_selection):
                return (change.first_page,)
    return (1,)


def _change_overlaps_line_change(change: ReviewChange, line_changes: LineLevelChange) -> bool:
    old_numbers = [
        line.old_line_number
        for line in line_changes.lines
        if line.kind != "+" and line.old_line_number is not None
    ]
    new_numbers = [
        line.new_line_number
        for line in line_changes.lines
        if line.kind != "-" and line.new_line_number is not None
    ]
    return (
        _ranges_overlap(change.old_start, change.old_end, min(old_numbers, default=None), max(old_numbers, default=None))
        or _ranges_overlap(change.new_start, change.new_end, min(new_numbers, default=None), max(new_numbers, default=None))
    )


def _ranges_overlap(
    left_start: int | None,
    left_end: int | None,
    right_start: int | None,
    right_end: int | None,
) -> bool:
    if left_start is None or left_end is None or right_start is None or right_end is None:
        return False
    return left_start <= right_end and right_start <= left_end


def _pages_containing_review_display_ids(
    model: FileReviewModel,
    display_ids: tuple[int, ...],
) -> tuple[int, ...]:
    """Return review pages containing all of the requested display IDs."""
    if model.display_id_by_selection_id is None:
        def display_id_for_row(row: LineEntry) -> int | None:
            return row.id
    else:
        def display_id_for_row(row: LineEntry) -> int | None:
            return model.display_id_by_selection_id.get(row.id)

    wanted = set(display_ids)
    found: set[int] = set()
    pages: set[int] = set()
    for page in model.pages:
        for fragment in page.changes:
            for row in fragment.rows:
                if row.id is None:
                    continue
                display_id = display_id_for_row(row)
                if display_id in wanted:
                    found.add(display_id)
                    pages.add(page.page)
    if found != wanted:
        return tuple()
    return tuple(sorted(pages))


def _change_index_containing_review_display_ids(
    model: FileReviewModel,
    display_ids: tuple[int, ...],
) -> int:
    """Return a stable nearby change index for supplemental review selections."""
    if model.display_id_by_selection_id is None:
        def display_id_for_row(row: LineEntry) -> int | None:
            return row.id
    else:
        def display_id_for_row(row: LineEntry) -> int | None:
            return model.display_id_by_selection_id.get(row.id)

    wanted = set(display_ids)
    for page in model.pages:
        for fragment in page.changes:
            row_display_ids = {
                display_id_for_row(row)
                for row in fragment.rows
                if row.id is not None
            }
            if wanted & row_display_ids:
                return fragment.change.index
    return 0


def _supplemental_batch_review_selections(
    model: FileReviewModel,
    *,
    visible_display_ids: set[int] | None,
) -> tuple[FileReviewSelectionState, ...]:
    """Persist reset ownership atoms without making them pagination changes."""
    selections: list[FileReviewSelectionState] = []
    primary_reset_groups = {
        change.display_ids
        for change in model.changes
        if FileReviewAction.RESET_FROM_BATCH.value in change.actions
    }
    for group in model.review_action_groups:
        if not group.display_ids:
            continue
        if group.display_ids in primary_reset_groups:
            continue
        display_id_set = set(group.display_ids)
        if visible_display_ids is not None and not display_id_set.issubset(visible_display_ids):
            continue
        if FileReviewAction.RESET_FROM_BATCH.value not in group.actions:
            continue
        pages = _pages_containing_review_display_ids(model, group.display_ids)
        if not pages:
            continue
        selections.append(
            FileReviewSelectionState(
                display_ids=group.display_ids,
                selection_ids=group.selection_ids,
                change_index=_change_index_containing_review_display_ids(
                    model,
                    group.display_ids,
                ),
                first_page=pages[0],
                last_page=pages[-1],
                reason=_coerce_actionable_reason(group.reason),
                actions=(FileReviewAction.RESET_FROM_BATCH,),
            )
        )
    return tuple(selections)


def make_file_review_state(
    model: FileReviewModel,
    *,
    source: ReviewSource,
    batch_name: str | None,
    shown_pages: tuple[int, ...],
    selected_change_kind: SelectedChangeKind,
    gutter_to_selection_id: dict[int, int] | None = None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None = None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None = None,
    visible_display_ids: set[int] | None = None,
    entire_file_shown: bool | None = None,
) -> FileReviewState:
    """Create persisted review state from a rendered page selection."""
    page_count = len(model.pages)
    shown_page_set = set(shown_pages)
    selection_actions = (
        (
            FileReviewAction.INCLUDE_FROM_BATCH,
            FileReviewAction.DISCARD_FROM_BATCH,
            FileReviewAction.APPLY_FROM_BATCH,
            FileReviewAction.RESET_FROM_BATCH,
        )
        if source == ReviewSource.BATCH
        else (
            FileReviewAction.INCLUDE,
            FileReviewAction.SKIP,
            FileReviewAction.DISCARD,
            FileReviewAction.INCLUDE_TO_BATCH,
            FileReviewAction.DISCARD_TO_BATCH,
        )
    )
    selections = []
    for change in model.changes:
        if not change.display_ids:
            continue
        if visible_display_ids is not None and not set(change.display_ids).issubset(visible_display_ids):
            continue
        change_actions = (
            tuple(FileReviewAction(action) for action in change.actions)
            if change.actions else
            selection_actions
        )
        selections.append(
            FileReviewSelectionState(
                display_ids=change.display_ids,
                selection_ids=change.selection_ids,
                change_index=change.index,
                first_page=change.first_page,
                last_page=change.last_page,
                reason=change.reason,
                actions=change_actions,
            )
        )
    if source == ReviewSource.BATCH and review_action_groups is not None:
        selections.extend(
            _supplemental_batch_review_selections(
                model,
                visible_display_ids=visible_display_ids,
            )
        )
    computed_entire_file_shown = shown_page_set == set(range(1, page_count + 1))
    return FileReviewState(
        source=source,
        batch_name=batch_name,
        file_path=model.line_changes.path,
        page_spec=normalize_page_spec(shown_pages, page_count),
        shown_pages=shown_pages,
        page_count=page_count,
        entire_file_shown=(
            computed_entire_file_shown
            if entire_file_shown is None else
            entire_file_shown
        ),
        selections=tuple(selections),
        selected_change_kind=selected_change_kind,
        selected_file_fingerprint=fingerprint_selected_file_view(
            source=source,
            batch_name=batch_name,
            file_path=model.line_changes.path,
            selected_change_kind=selected_change_kind,
            gutter_to_selection_id=gutter_to_selection_id,
            actionable_selection_groups=actionable_selection_groups,
            review_action_groups=review_action_groups,
        ),
        diff_fingerprint=compute_current_file_review_diff_fingerprint(model.line_changes.path),
    )


def _display_ids_for_rows(
    rows: tuple[LineEntry, ...],
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[int, ...]:
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


def _line_spec_for_display_ids(display_ids: tuple[int, ...]) -> str:
    if not display_ids:
        return "-"
    return _display_line_spec(format_line_ids(list(display_ids)))


def _change_spec_for_fragments(fragments: list[ReviewChangeFragment]) -> str:
    change_ids: list[int] = []
    seen: set[int] = set()
    for fragment in fragments:
        change_id = fragment.change.index
        if change_id in seen:
            continue
        change_ids.append(change_id)
        seen.add(change_id)
    if not change_ids:
        return "-"
    return _display_line_spec(format_line_ids(change_ids))


def print_file_review(
    model: FileReviewModel,
    *,
    shown_pages: tuple[int, ...],
    source_label: str,
    page_spec: str,
    command_source_args: str = "",
    source: ReviewSource,
    batch_name: str | None = None,
    note: str | None = None,
    opened_near_selected_hunk: bool = False,
) -> None:
    """Print a page-aware file review."""
    page_count = len(model.pages)
    shown_page_set = set(shown_pages)
    shown_fragments = [
        fragment
        for page in shown_pages
        for fragment in model.pages[page - 1].changes
    ]
    shown_changes = []
    seen_change_indexes: set[int] = set()
    for fragment in shown_fragments:
        if fragment.change.index in seen_change_indexes:
            continue
        shown_changes.append(fragment.change)
        seen_change_indexes.add(fragment.change.index)
    shown_display_ids = []
    seen_display_ids: set[int] = set()
    for fragment in shown_fragments:
        for display_id in _display_ids_for_rows(fragment.rows, model.display_id_by_selection_id):
            if display_id in seen_display_ids:
                continue
            shown_display_ids.append(display_id)
            seen_display_ids.add(display_id)
    shown_line_spec = _line_spec_for_display_ids(tuple(shown_display_ids))
    shown_change_spec = _change_spec_for_fragments(shown_fragments)
    complete_changes = [
        change
        for change in shown_changes
        if change.display_ids
        if set(range(change.first_page, change.last_page + 1)).issubset(shown_page_set)
    ]

    _print_header(
        model.line_changes.path,
        source_label=source_label,
        source=source,
        batch_name=batch_name,
        note=note,
        shown_pages=shown_pages,
        page_count=page_count,
        shown_change_spec=shown_change_spec,
        shown_line_spec=shown_line_spec,
        total_changes=len(model.changes),
        opened_near_selected_hunk=opened_near_selected_hunk,
    )

    multi_page = len(shown_pages) > 1
    for page in shown_pages:
        if multi_page:
            print()
            print(f"── page {page}/{page_count} " + "─" * 48)
        for fragment in model.pages[page - 1].changes:
            change = fragment.change
            print()
            fragment_display_ids = _display_ids_for_rows(
                fragment.rows,
                model.display_id_by_selection_id,
            )
            selection_spec = (
                _line_spec_for_display_ids(fragment_display_ids)
                if fragment_display_ids else
                change.select_as or "-"
            )
            if change.display_ids:
                line_count = len(fragment_display_ids) if fragment_display_ids else len(change.display_ids)
                size_label = (
                    _("1-line change")
                    if line_count == 1 else
                    _("{count}-line partial group").format(count=line_count)
                    if not fragment.is_first_fragment or not fragment.is_last_fragment else
                    _("{count}-line group").format(count=line_count)
                )
                print(
                    _("Change {index}/{total}   lines {lines}   {size}").format(
                        index=change.index,
                        total=change.total,
                        lines=selection_spec,
                        size=size_label,
                    )
                )
            else:
                print(
                    _("Change {index}/{total}   {note}").format(
                        index=change.index,
                        total=change.total,
                        note=change.note or _("not currently selectable"),
                    )
                )
            _print_rows(
                fragment.rows,
                _maximum_display_id_digit_count(model),
                display_id_by_selection_id=model.display_id_by_selection_id,
                allowed_selection_ids=set(change.selection_ids) if change.display_ids else set(),
            )

    _print_footer(
        model.line_changes.path,
        shown_pages=shown_pages,
        page_count=page_count,
        shown_change_spec=shown_change_spec,
        shown_line_spec=shown_line_spec,
        complete_changes=complete_changes,
        total_changes=len(model.changes),
        page_spec=page_spec,
        command_source_args=command_source_args,
        source=source,
        batch_name=batch_name,
    )


def _change_supports_action(change: ReviewChange, action: FileReviewAction) -> bool:
    """Return whether a rendered review change supports an action."""
    return not change.actions or action.value in change.actions


def _review_change_heading_action(change: ReviewChange) -> str:
    """Return the concise action label shown for one review change."""
    if change.reason == ActionableSelectionReason.REPLACEMENT:
        return _("select together")
    if change.actions == (FileReviewAction.RESET_FROM_BATCH.value,):
        return _("reset")
    return _("select")


def _display_line_spec(line_spec: str) -> str:
    return line_spec.replace("-", "–")


def _page_summary(shown_pages: tuple[int, ...], page_count: int) -> str:
    page_word = _("page") if len(shown_pages) == 1 else _("pages")
    return _("{page_word} {pages}/{page_count}").format(
        page_word=page_word,
        pages=_display_line_spec(format_line_ids(list(shown_pages))),
        page_count=page_count,
    )


def _change_summary(change_spec: str, total_changes: int) -> str:
    change_word = _("change") if "," not in change_spec and "–" not in change_spec else _("changes")
    return _("{change_word} {changes}/{total}").format(
        change_word=change_word,
        changes=change_spec,
        total=total_changes,
    )


def _review_source_summary(
    source: ReviewSource,
    batch_name: str | None,
    source_label: str,
) -> str:
    if source == ReviewSource.BATCH and batch_name:
        return batch_name
    prefix = _("Changes: ")
    if source_label.startswith(prefix):
        return source_label[len(prefix):]
    return source_label


def _print_header(
    path: str,
    *,
    source_label: str,
    source: ReviewSource,
    batch_name: str | None,
    note: str | None,
    shown_pages: tuple[int, ...],
    page_count: int,
    shown_change_spec: str,
    shown_line_spec: str,
    total_changes: int,
    opened_near_selected_hunk: bool,
) -> None:
    use_color = Colors.enabled()
    status = "  ·  ".join(
        (
            path,
            _review_source_summary(source, batch_name, source_label),
            _page_summary(shown_pages, page_count),
            _change_summary(shown_change_spec, total_changes),
            _("lines {lines}").format(lines=shown_line_spec),
        )
    )
    if use_color:
        print(f"{Colors.BOLD}{status}{Colors.RESET}")
    else:
        print(status)
    if opened_near_selected_hunk:
        message = _("Showing the area around the change you were viewing.")
        print(f"{Colors.GRAY}{message}{Colors.RESET}" if use_color else message)
    if note:
        note_lines = note.splitlines()
        if len(note_lines) == 1:
            note_text = _("Note: {note}").format(note=note_lines[0])
            print(f"{Colors.GRAY}{note_text}{Colors.RESET}" if use_color else note_text)
        else:
            note_label = _("Note:")
            print(f"{Colors.GRAY}{note_label}{Colors.RESET}" if use_color else note_label)
            for line in note_lines:
                print(f"    {line}")
    rule = "─" * 78
    print(f"{Colors.GRAY}{rule}{Colors.RESET}" if use_color else rule)


def _maximum_display_id_digit_count(model: FileReviewModel) -> int:
    if model.display_id_by_selection_id is None:
        return model.line_changes.maximum_line_id_digit_count()
    if not model.display_id_by_selection_id:
        return 1
    return len(str(max(model.display_id_by_selection_id.values())))


def _print_rows(
    rows: tuple[LineEntry, ...],
    maximum_digits: int,
    *,
    display_id_by_selection_id: dict[int, int] | None,
    allowed_selection_ids: set[int] | None = None,
) -> None:
    use_color = Colors.enabled()
    label_width = maximum_digits + 3
    for line in rows:
        is_gap_line = (
            line.id is None
            and line.kind == " "
            and line.old_line_number is None
            and line.new_line_number is None
            and line.source_line is None
        )
        if line.id is None or (
            allowed_selection_ids is not None
            and line.id not in allowed_selection_ids
        ):
            display_id = None
        elif display_id_by_selection_id is not None:
            display_id = display_id_by_selection_id.get(line.id)
        else:
            display_id = line.id
        if display_id is None:
            label = ""
        else:
            label = f"[#{display_id}]"
        padding = " " * max(0, label_width - len(label))
        row_text = f" {line.kind} {line.display_text()}"
        if not use_color:
            print(f"{label}{padding}{row_text}")
            continue

        if label:
            print(f"{Colors.GRAY}{label}{Colors.RESET}{padding}", end="")
        else:
            print(padding, end="")

        if line.kind == "+":
            print(f"{Colors.GREEN}{row_text}{Colors.RESET}")
        elif line.kind == "-":
            print(f"{Colors.RED}{row_text}{Colors.RESET}")
        elif is_gap_line:
            print(f"{Colors.GRAY}{row_text}{Colors.RESET}")
        else:
            print(row_text)


def _style_footer_command(command: str) -> str:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return command

    dynamic_value_options = {"--file", "--from", "--line", "--page", "--to"}
    if tokens[:2] == ["git", "stage-batch"]:
        action_index = 2
    elif tokens[:1] == ["git-stage-batch"]:
        action_index = 1
    else:
        action_index = -1
    styled_tokens: list[str] = []
    bold_next = False
    for index, token in enumerate(tokens):
        should_bold = bold_next or index == action_index
        if should_bold:
            styled_tokens.append(f"{Colors.BOLD}{token}{Colors.RESET}")
        else:
            styled_tokens.append(token)
        bold_next = token in dynamic_value_options
    return " ".join(styled_tokens)


def _invoked_command_prefix() -> str:
    executable = os.path.basename(sys.argv[0])
    if executable == "git" and len(sys.argv) > 1 and sys.argv[1] == "stage-batch":
        return "git stage-batch"
    return "git-stage-batch"


def _display_footer_command(command: str) -> str:
    if command.startswith("git-stage-batch "):
        return _invoked_command_prefix() + command[len("git-stage-batch"):]
    return command


def _print_footer(
    path: str,
    *,
    shown_pages: tuple[int, ...],
    page_count: int,
    shown_change_spec: str,
    shown_line_spec: str,
    complete_changes: list[ReviewChange],
    total_changes: int,
    page_spec: str,
    command_source_args: str,
    source: ReviewSource,
    batch_name: str | None,
) -> None:
    shown = set(shown_pages)
    is_entire = shown == set(range(1, page_count + 1))
    hints: list[tuple[str, str]] = []
    navigation_hints: list[tuple[str, str]] = []

    review_state = _footer_review_state(
        path=path,
        shown_pages=shown_pages,
        page_count=page_count,
        source=source,
        batch_name=batch_name,
    )
    primary_action = (
        FileReviewAction.INCLUDE_FROM_BATCH
        if source == ReviewSource.BATCH else
        FileReviewAction.INCLUDE
    )
    primary_changes = [
        change
        for change in complete_changes
        if _change_supports_action(change, primary_action)
    ]
    reset_changes = [
        change
        for change in complete_changes
        if _change_supports_action(change, FileReviewAction.RESET_FROM_BATCH)
    ]

    if primary_changes:
        combined_selection = ",".join(format_line_ids(list(change.display_ids)) for change in primary_changes)
        include_line_command = (
            _line_action_command("include", review_state, line_spec=combined_selection, pathless_line=True)
            if review_state is not None else
            None
        )
        hints.append(
            (
                _("include"),
                include_line_command or f"git-stage-batch include{command_source_args} --line {combined_selection}",
            )
        )
        if not command_source_args:
            skip_line_command = (
                _line_action_command("skip", review_state, line_spec=combined_selection, pathless_line=True)
                if review_state is not None else
                None
            )
            hints.append(
                (
                    _("skip"),
                    skip_line_command or f"git-stage-batch skip --line {combined_selection}",
                )
            )
        discard_line_command = (
            _line_action_command("discard", review_state, line_spec=combined_selection, pathless_line=True)
            if review_state is not None else
            None
        )
        hints.append(
            (
                _("discard"),
                discard_line_command or f"git-stage-batch discard{command_source_args} --line {combined_selection}",
            )
        )
        if source == ReviewSource.BATCH and reset_changes:
            reset_selection = ",".join(format_line_ids(list(change.display_ids)) for change in reset_changes)
            reset_line_command = _line_action_command(
                FileReviewAction.RESET_FROM_BATCH,
                review_state,
                line_spec=reset_selection,
                pathless_line=True,
            )
            hints.append(
                (
                    _("reset"),
                    reset_line_command or f"git-stage-batch reset{command_source_args} --line {reset_selection}",
                )
            )
    elif reset_changes:
        reset_selection = ",".join(format_line_ids(list(change.display_ids)) for change in reset_changes)
        reset_line_command = _line_action_command(
            FileReviewAction.RESET_FROM_BATCH,
            review_state,
            line_spec=reset_selection,
            pathless_line=True,
        )
        hints.append(
            (
                _("reset"),
                reset_line_command or f"git-stage-batch reset{command_source_args} --line {reset_selection}",
            )
        )
    elif not is_entire:
        hints.append((_("No complete change is actionable from this page."), ""))

    if not is_entire and max(shown_pages) < page_count:
        navigation_hints.append(
            (
                _("next"),
                f"git-stage-batch show{command_source_args} --file {_quote(path)} --page {max(shown_pages) + 1}",
            )
        )

    if is_entire:
        hints.append(
            (
                _("include"),
                f"git-stage-batch include{command_source_args} --file {_quote(path)}",
            )
        )
    else:
        navigation_hints.append(
            (
                _("all"),
                f"git-stage-batch show{command_source_args} --file {_quote(path)} --page all",
            )
        )

    hints.extend(navigation_hints)
    if hints:
        print()
        use_color = Colors.enabled()
        rule = "─" * 78
        print(f"{Colors.GRAY}{rule}{Colors.RESET}" if use_color else rule)
        status = "  ·  ".join(
            (
                path,
                _page_summary(shown_pages, page_count),
                _change_summary(shown_change_spec, total_changes),
                _("lines {lines}").format(lines=shown_line_spec),
            )
        )
        if use_color:
            print(f"{Colors.BOLD}{status}{Colors.RESET}")
        else:
            print(status)
        print()
        action_width = max(len(action) for action, command in hints if command)
        for action, command in hints:
            if not command:
                print(action)
                continue
            display_command = _display_footer_command(command)
            action_text = action.ljust(action_width)
            if use_color:
                print(f"{Colors.CYAN}{action_text}{Colors.RESET}  {_style_footer_command(display_command)}")
            else:
                print(f"{action_text}  {display_command}")


def _footer_review_state(
    *,
    path: str,
    shown_pages: tuple[int, ...],
    page_count: int,
    source: ReviewSource,
    batch_name: str | None,
) -> FileReviewState:
    if source == ReviewSource.BATCH:
        return FileReviewState(
            source=ReviewSource.BATCH,
            batch_name=batch_name,
            file_path=path,
            page_spec="all",
            shown_pages=shown_pages,
            page_count=page_count,
            entire_file_shown=set(shown_pages) == set(range(1, page_count + 1)),
            selections=tuple(),
            selected_change_kind=SelectedChangeKind.BATCH_FILE,
            selected_file_fingerprint="",
            diff_fingerprint="",
        )
    return FileReviewState(
        source=source,
        batch_name=None,
        file_path=path,
        page_spec="all",
        shown_pages=shown_pages,
        page_count=page_count,
        entire_file_shown=set(shown_pages) == set(range(1, page_count + 1)),
        selections=tuple(),
        selected_change_kind=SelectedChangeKind.FILE,
        selected_file_fingerprint="",
        diff_fingerprint="",
    )
