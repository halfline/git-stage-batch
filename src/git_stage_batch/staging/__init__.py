"""Line-level staging operations (compatibility re-exports)."""

# Re-export from new modular location for backward compatibility during Pass 1
from .operations import (
    build_target_index_content_with_selected_lines,
    build_target_working_tree_content_with_discarded_lines,
    update_index_with_blob_content,
)

__all__ = [
    "build_target_index_content_with_selected_lines",
    "build_target_working_tree_content_with_discarded_lines",
    "update_index_with_blob_content",
]
