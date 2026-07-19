"""Live working-tree diff streams."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ..core.buffer import LineBuffer
from ..core.diff_parser import UnifiedDiffItem, acquire_unified_diff
from ..core.models import SingleHunkPatch
from ..utils.git_command import stream_git_diff


def stream_live_git_diff(**kwargs):
    """Stream actionable live changes with rename detection enabled."""
    kwargs.setdefault("find_renames", True)
    return stream_git_diff(**kwargs)


@contextmanager
def acquire_prepared_live_diff(**kwargs) -> Iterator[tuple[UnifiedDiffItem, ...]]:
    """Acquire one reusable live diff while owning cloned hunk buffers."""
    owned_buffers: list[LineBuffer] = []
    changes: list[UnifiedDiffItem] = []
    try:
        with acquire_unified_diff(stream_live_git_diff(**kwargs)) as parsed:
            for change in parsed:
                if isinstance(change, SingleHunkPatch):
                    lines = change.lines
                    if not isinstance(lines, LineBuffer):
                        raise TypeError("parsed text hunk must use LineBuffer storage")
                    cloned_lines = lines.clone()
                    owned_buffers.append(cloned_lines)
                    change = SingleHunkPatch(
                        old_path=change.old_path,
                        new_path=change.new_path,
                        lines=cloned_lines,
                    )
                changes.append(change)
        yield tuple(changes)
    finally:
        for buffer in owned_buffers:
            buffer.close()
