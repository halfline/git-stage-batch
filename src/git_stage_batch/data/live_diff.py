"""Live working-tree diff streams."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
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


def group_live_diff_by_file(
    files: Sequence[str],
    changes: Sequence[UnifiedDiffItem],
) -> dict[str, tuple[UnifiedDiffItem, ...]]:
    """Assign each live change once to its canonical requested file."""
    requested_files = set(files)
    grouped: dict[str, list[UnifiedDiffItem]] = {file_path: [] for file_path in files}
    for change in changes:
        preferred_path = change.path()
        if preferred_path in requested_files:
            grouped[preferred_path].append(change)
            continue
        change_paths = (
            getattr(change, "old_path", None),
            getattr(change, "new_path", None),
        )
        for file_path in files:
            if file_path in change_paths:
                grouped[file_path].append(change)
                break
    return {
        file_path: tuple(file_changes) for file_path, file_changes in grouped.items()
    }
