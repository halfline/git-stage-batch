"""Data management modules."""

from __future__ import annotations

from .batch_refs import restore_batch_refs, snapshot_batch_refs
from .file_tracking import auto_add_untracked_files
from .hunk_tracking import (
    format_id_range,
    record_hunk_discarded,
    record_hunk_included,
    record_hunk_skipped,
)

__all__ = [
    "auto_add_untracked_files",
    "format_id_range",
    "record_hunk_discarded",
    "record_hunk_included",
    "record_hunk_skipped",
    "restore_batch_refs",
    "snapshot_batch_refs",
]
