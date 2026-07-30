"""Test-only inspection helpers for line-buffer storage."""

from __future__ import annotations

import mmap

from git_stage_batch.core.buffer import LineBuffer


def uses_mapped_storage(buffer: LineBuffer) -> bool:
    """Return whether an open test buffer uses memory-mapped storage."""
    return isinstance(buffer._data, mmap.mmap) and not buffer._closed
