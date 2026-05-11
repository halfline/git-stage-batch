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
from .edit import edit_lines_as_buffer

__all__ = [
    "EditorBuffer",
    "BufferInput",
    "choose_line_ending",
    "buffer_byte_chunks",
    "buffer_byte_count",
    "buffer_has_data",
    "buffer_matches",
    "buffer_preview",
    "detect_line_ending",
    "edit_lines_as_buffer",
    "restore_line_endings",
    "restore_line_endings_in_chunks",
    "write_buffer_to_path",
    "write_buffer_to_working_tree_path",
]
