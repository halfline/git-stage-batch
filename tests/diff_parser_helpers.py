"""Test helpers for unified diff parser assertions."""

from __future__ import annotations

from collections.abc import Iterable

from git_stage_batch.core.diff_parser import UnifiedDiffItem, acquire_unified_diff
from git_stage_batch.core.models import SingleHunkPatch


def collect_unified_diff(lines: Iterable[bytes]) -> list[UnifiedDiffItem]:
    """Collect scoped diff items with text hunk payloads materialized."""
    collected = []
    with acquire_unified_diff(lines) as items:
        for item in items:
            if isinstance(item, SingleHunkPatch):
                collected.append(
                    SingleHunkPatch(
                        old_path=item.old_path,
                        new_path=item.new_path,
                        lines=list(item.lines),
                    )
                )
            else:
                collected.append(item)
    return collected
