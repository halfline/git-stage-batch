"""Tests for batch matcher workspace storage ownership."""

from __future__ import annotations

import pytest

import git_stage_batch.batch.line_matching.match as match_module
import git_stage_batch.core.mapped_storage as mapped_storage_module
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace


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


def test_match_lines_routes_mapped_storage_to_requested_spool(
    tmp_path,
    monkeypatch,
):
    """Mappings and matcher scratch should stay in invocation-owned storage."""
    temporary_directories = []
    real_temporary_file = mapped_storage_module._temporary_file

    def recording_temporary_file(spool_dir=None):
        temporary_directories.append(spool_dir)
        return real_temporary_file(spool_dir)

    monkeypatch.setattr(
        mapped_storage_module,
        "_temporary_file",
        recording_temporary_file,
    )
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()
    lines = [f"line {index}\n".encode() for index in range(2_000)]

    with match_lines(lines, lines, spool_dir=spool_dir) as mapping:
        assert len(tuple(mapping.mapped_line_pairs())) == len(lines)

    assert temporary_directories
    assert all(
        directory is not None
        and directory.resolve() == spool_dir.resolve()
        for directory in temporary_directories
    )


def test_match_lines_closes_first_mapping_if_second_allocation_fails(
    monkeypatch,
):
    """Partial mapping allocation should not leak the first mapped vector."""
    class ClosingVector(list):
        closed = False

        def close(self):
            self.closed = True

    source_mapping = ClosingVector([0])
    calls = 0

    def allocate_mapping(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return source_mapping
        raise KeyboardInterrupt

    monkeypatch.setattr(
        match_module,
        "_new_line_mapping",
        allocate_mapping,
    )

    with pytest.raises(KeyboardInterrupt):
        match_lines([b"line\n"], [b"line\n"])

    assert source_mapping.closed is True
