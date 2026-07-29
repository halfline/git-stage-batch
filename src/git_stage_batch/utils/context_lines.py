"""Read the persisted unified-diff context setting."""

from __future__ import annotations

from .file_io import read_text_file_contents
from .paths import get_context_lines_file_path


def get_context_lines() -> int:
    """Return the stored context-line count, defaulting to three."""
    context_file = get_context_lines_file_path()
    if not context_file.exists():
        return 3

    try:
        return int(read_text_file_contents(context_file).strip())
    except ValueError:
        return 3
