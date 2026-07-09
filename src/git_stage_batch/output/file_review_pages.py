"""File-review page selection helpers."""

from __future__ import annotations

from ..core.line_selection import format_line_ids, parse_positive_selection
from ..exceptions import CommandError
from ..i18n import _


def parse_page_selection(
    page_spec: str,
    page_count: int,
    file_path: str,
) -> tuple[int, ...]:
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
            _(
                "Page {page} is outside the file review for {file}.\n"
                "Available pages: 1-{page_count}."
            ).format(
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
