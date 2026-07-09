"""Terminal layout sizing for file-review rendering."""

from __future__ import annotations

import shutil


DEFAULT_NON_TTY_REVIEW_LINES = 80
DEFAULT_REVIEW_WIDTH = 80
REVIEW_HEADER_LINES = 3
REVIEW_FOOTER_LINES = 9
PAGER_EXIT_MARGIN_LINES = 1
MINIMUM_BODY_LINES = 8


def body_budget() -> int:
    """Return the number of terminal rows available for review body content."""
    size = review_terminal_size()
    estimated_footer_lines = estimate_file_review_footer_height()
    reserved_lines = (
        REVIEW_HEADER_LINES + estimated_footer_lines + PAGER_EXIT_MARGIN_LINES
    )
    return max(MINIMUM_BODY_LINES, size.lines - reserved_lines)


def review_terminal_size():
    """Return the terminal size used for file-review layout."""
    return shutil.get_terminal_size(
        fallback=(DEFAULT_REVIEW_WIDTH, DEFAULT_NON_TTY_REVIEW_LINES)
    )


def estimate_file_review_footer_height(_complete_change_count: int = 3) -> int:
    """Return the expected footer height for file-review layout."""
    return REVIEW_FOOTER_LINES
