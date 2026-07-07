"""Live working-tree diff streams."""

from __future__ import annotations

from ..utils.git import stream_git_diff


def stream_live_git_diff(**kwargs):
    """Stream actionable live changes with rename detection enabled."""
    kwargs.setdefault("find_renames", True)
    return stream_git_diff(**kwargs)
