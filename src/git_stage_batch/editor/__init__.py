"""Line editing, Git buffer loading, and line-ending helpers."""

from .line_endings import (
    choose_line_ending,
    detect_line_ending,
    restore_line_endings,
    restore_line_endings_in_chunks,
)
from .edit import Cursor, Editor, edit_lines_as_buffer, export_lines_as_buffer
from .git import (
    load_git_blob_as_buffer,
    load_git_object_as_buffer,
    load_git_object_as_buffer_or_empty,
    load_git_tree_files_as_buffers,
    load_working_tree_file_as_buffer,
)

__all__ = [
    "Cursor",
    "Editor",
    "choose_line_ending",
    "detect_line_ending",
    "load_git_blob_as_buffer",
    "load_git_object_as_buffer",
    "load_git_object_as_buffer_or_empty",
    "load_git_tree_files_as_buffers",
    "load_working_tree_file_as_buffer",
    "edit_lines_as_buffer",
    "export_lines_as_buffer",
    "restore_line_endings",
    "restore_line_endings_in_chunks",
]
