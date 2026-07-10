"""Pagination for file review output models."""

from __future__ import annotations

from ..core.models import LineEntry
from . import file_review_layout
from .file_review_model import FileReviewPage, ReviewChange, ReviewChangeFragment


def paginate_file_review_changes(
    changes: tuple[ReviewChange, ...],
) -> tuple[tuple[ReviewChange, ...], tuple[FileReviewPage, ...]]:
    """Return page-aware changes and pages for file-review output."""
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
    return tuple(paged_changes), final_pages
