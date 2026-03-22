"""Batch operations (compatibility re-exports)."""

# Re-export from modular structure for backward compatibility during Pass 1
from .operations import create_batch, delete_batch, update_batch_note
from .query import (
    get_batch_baseline_commit,
    get_batch_commit_sha,
    get_batch_tree_sha,
    list_batch_files,
    list_batch_names,
    read_batch_metadata,
)
from .storage import add_file_to_batch, get_batch_diff, read_file_from_batch
from .validation import batch_exists, validate_batch_name

__all__ = [
    "add_file_to_batch",
    "batch_exists",
    "create_batch",
    "delete_batch",
    "get_batch_baseline_commit",
    "get_batch_commit_sha",
    "get_batch_diff",
    "get_batch_tree_sha",
    "list_batch_files",
    "list_batch_names",
    "read_batch_metadata",
    "read_file_from_batch",
    "update_batch_note",
    "validate_batch_name",
]
