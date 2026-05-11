"""Editor buffers and line editing helpers."""

from .buffer import (
    EditorBuffer,
    BufferInput,
    buffer_byte_chunks,
    buffer_byte_count,
    buffer_has_data,
    buffer_matches,
    buffer_preview,
    write_buffer_to_path,
    write_buffer_to_working_tree_path,
)
from .line_endings import (
    choose_line_ending,
    detect_line_ending,
    restore_line_endings,
    restore_line_endings_in_chunks,
)
from .edit import Cursor, Editor, edit_lines_as_buffer
from .git import (
    load_git_blob_as_buffer,
    load_git_object_as_buffer,
    load_git_object_as_buffer_or_empty,
    load_git_tree_files_as_buffers,
    load_working_tree_file_as_buffer,
)

__all__ = [
    "EditorBuffer",
    "BufferInput",
    "Cursor",
    "Editor",
    "choose_line_ending",
    "buffer_byte_chunks",
    "buffer_byte_count",
    "buffer_has_data",
    "buffer_matches",
    "buffer_preview",
    "detect_line_ending",
    "load_git_blob_as_buffer",
    "load_git_object_as_buffer",
    "load_git_object_as_buffer_or_empty",
    "load_git_tree_files_as_buffers",
    "load_working_tree_file_as_buffer",
    "edit_lines_as_buffer",
    "restore_line_endings",
    "restore_line_endings_in_chunks",
    "write_buffer_to_path",
    "write_buffer_to_working_tree_path",
]
