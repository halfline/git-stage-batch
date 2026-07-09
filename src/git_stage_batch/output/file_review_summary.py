"""File-review summary labels and selection specs."""

from __future__ import annotations

from ..core.line_selection import format_line_ids
from ..data.file_review.records import ReviewSource
from ..i18n import _
from .file_review_model import ReviewChangeFragment


def display_line_spec(line_spec: str) -> str:
    """Return a display-formatted line or page range."""
    return line_spec.replace("-", "–")


def line_spec_for_display_ids(display_ids: tuple[int, ...]) -> str:
    """Return a footer/header line spec for display IDs."""
    if not display_ids:
        return "-"
    return display_line_spec(format_line_ids(list(display_ids)))


def change_spec_for_fragments(fragments: list[ReviewChangeFragment]) -> str:
    """Return a summary spec for the unique changes in shown fragments."""
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
    return display_line_spec(format_line_ids(change_ids))


def page_summary(shown_pages: tuple[int, ...], page_count: int) -> str:
    """Return a concise shown-page summary."""
    page_word = _("page") if len(shown_pages) == 1 else _("pages")
    return _("{page_word} {pages}/{page_count}").format(
        page_word=page_word,
        pages=display_line_spec(format_line_ids(list(shown_pages))),
        page_count=page_count,
    )


def change_summary(change_spec: str, total_changes: int) -> str:
    """Return a concise shown-change summary."""
    change_word = (
        _("change")
        if "," not in change_spec and "–" not in change_spec else
        _("changes")
    )
    return _("{change_word} {changes}/{total}").format(
        change_word=change_word,
        changes=change_spec,
        total=total_changes,
    )


def review_source_summary(
    source: ReviewSource,
    batch_name: str | None,
    source_label: str,
) -> str:
    """Return the source label used in file-review status lines."""
    if source == ReviewSource.BATCH and batch_name:
        return batch_name
    prefix = _("Changes: ")
    if source_label.startswith(prefix):
        return source_label[len(prefix):]
    return source_label
