"""Page-aware file review rendering."""

from __future__ import annotations

from ..data.file_review.records import ReviewSource
from ..i18n import _
from .colors import Colors
from .file_review_action_selections import shown_line_action_selections
from .file_review_display_ids import display_ids_for_rows
from .file_review_footer import print_file_review_footer
from .file_review_model import FileReviewModel
from .file_review_rows import (
    maximum_display_id_digit_count,
    print_file_review_rows,
)
from .file_review_summary import (
    change_spec_for_fragments,
    change_summary,
    line_spec_for_display_ids,
    page_summary,
    review_source_summary,
)


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
        for display_id in display_ids_for_rows(
            fragment.rows,
            model.display_id_by_selection_id,
        ):
            if display_id in seen_display_ids:
                continue
            shown_display_ids.append(display_id)
            seen_display_ids.add(display_id)
    shown_line_spec = line_spec_for_display_ids(tuple(shown_display_ids))
    shown_change_spec = change_spec_for_fragments(shown_fragments)
    complete_line_action_selections = shown_line_action_selections(
        model,
        shown_pages,
        source=source,
    )

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
            fragment_display_ids = display_ids_for_rows(
                fragment.rows,
                model.display_id_by_selection_id,
            )
            selection_spec = (
                line_spec_for_display_ids(fragment_display_ids)
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
            print_file_review_rows(
                fragment.rows,
                maximum_display_id_digit_count(model),
                display_id_by_selection_id=model.display_id_by_selection_id,
                allowed_selection_ids=set(change.selection_ids) if change.display_ids else set(),
            )

    print_file_review_footer(
        model.line_changes.path,
        shown_pages=shown_pages,
        page_count=page_count,
        shown_change_spec=shown_change_spec,
        shown_line_spec=shown_line_spec,
        complete_line_action_selections=complete_line_action_selections,
        total_changes=len(model.changes),
        command_source_args=command_source_args,
        source=source,
        batch_name=batch_name,
    )


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
            review_source_summary(source, batch_name, source_label),
            page_summary(shown_pages, page_count),
            change_summary(shown_change_spec, total_changes),
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
