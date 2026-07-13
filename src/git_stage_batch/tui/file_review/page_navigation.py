"""Page navigation prompts for TUI file review."""

from __future__ import annotations

import sys

from ...data.file_review.state import read_last_file_review_state
from ...i18n import _
from ..prompts import unlocked_input, wrap_prompt_for_readline


def prompt_page_spec() -> str | None:
    """Prompt for an explicit file-review page specification."""
    try:
        value = unlocked_input(
            wrap_prompt_for_readline(_("Page(s), for example 1, 2-4, all: "))
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return None
    return value or None


def next_page_spec() -> str | None:
    """Return the next persisted file-review page specification."""
    review_state = read_last_file_review_state()
    if review_state is None:
        print(_("No file review page state is available."), file=sys.stderr)
        return None

    current_page = max(review_state.shown_pages)
    if current_page >= review_state.page_count:
        print(_("Already at the last file review page."), file=sys.stderr)
        return review_state.page_spec

    return str(current_page + 1)


def previous_page_spec() -> str | None:
    """Return the previous persisted file-review page specification."""
    review_state = read_last_file_review_state()
    if review_state is None:
        print(_("No file review page state is available."), file=sys.stderr)
        return None

    current_page = min(review_state.shown_pages)
    if current_page <= 1:
        print(_("Already at the first file review page."), file=sys.stderr)
        return review_state.page_spec

    return str(current_page - 1)
