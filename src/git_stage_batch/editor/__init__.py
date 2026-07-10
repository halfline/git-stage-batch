"""Line editing and line-ending helpers."""

from .line_endings import (
    choose_line_ending,
    detect_line_ending,
    restore_line_endings,
    restore_line_endings_in_chunks,
)
from .edit import Cursor, Editor, edit_lines_as_buffer, export_lines_as_buffer

__all__ = [
    "Cursor",
    "Editor",
    "choose_line_ending",
    "detect_line_ending",
    "edit_lines_as_buffer",
    "export_lines_as_buffer",
    "restore_line_endings",
    "restore_line_endings_in_chunks",
]
