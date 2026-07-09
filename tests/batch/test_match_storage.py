"""Tests for batch matcher workspace storage ownership."""

from __future__ import annotations

from git_stage_batch.batch.match_storage import MatcherWorkspace


def test_matcher_workspace_tracks_and_closes_resources():
    """Matcher workspaces close all vectors they allocate."""
    workspace = MatcherWorkspace()
    vector = workspace.int_vector(2, width=4, fill=1)
    records = workspace.record_vector(1, "QQ")
    records.append((2, 3))

    assert workspace.current_bytes == vector.byte_count + records.byte_count
    assert workspace.high_water_bytes == workspace.current_bytes

    workspace.close_resource(vector)
    assert vector.closed
    assert workspace.current_bytes == records.byte_count

    workspace.close()
    assert records.closed
    assert workspace.current_bytes == 0
