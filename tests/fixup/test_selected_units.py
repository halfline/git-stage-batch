"""Tests for exact selected-line fixup-unit materialization."""

from __future__ import annotations

import gc
import subprocess
import tracemalloc

import pytest

from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.data.file_hunk_display import render_file_as_single_hunk
from git_stage_batch.fixup.selected_units import acquire_selected_fixup_unit
from git_stage_batch.fixup.commutation import tree_for_commit


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def selected_unit_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    (tmp_path / "README.md").write_text("base\n")
    _git("add", "README.md")
    _git("commit", "-m", "Base")
    return tmp_path


def test_selected_materialization_avoids_line_scale_python_heap(
    selected_unit_repo,
):
    """Selection ranges and exact patches stay bounded for large hunks."""
    heap_peaks: list[int] = []

    # Both samples exceed the fixed one-megabyte streaming chunk so the
    # comparison measures growth rather than crossing that constant buffer.
    for line_count in (65536, 131072):
        path = selected_unit_repo / f"large-{line_count}.txt"
        path.write_bytes(b"original payload\n" * line_count)
        _git("add", path.name)
        _git("commit", "-m", f"Add {path.name}")
        path.write_bytes(b"corrected payload\n" * line_count)
        line_changes = render_file_as_single_hunk(path.name)
        assert line_changes is not None
        maximum_id = max(
            line.id or 0
            for line in line_changes.lines
        )
        selected_ids = LineRanges.from_ranges(((1, maximum_id),))

        gc.collect()
        tracemalloc.start()
        try:
            with acquire_selected_fixup_unit(
                line_changes,
                selected_ids,
                source_tree=tree_for_commit("HEAD"),
            ) as unit:
                assert unit.kind == "text-replacement"
                assert unit.lineage_ranges == ((1, line_count),)
                assert unit.patch_buffer is not None
                assert unit.patch_buffer.byte_count > line_count * 16
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)

        _git("restore", path.name)

    small_peak, large_peak = heap_peaks
    # Interpreter bookkeeping varies slightly, but input growth stays bounded
    # by a fixed allowance rather than the number of selected lines.
    assert large_peak < small_peak + 160 * 1024


def test_selected_whole_file_addition_is_explicitly_unsupported(
    selected_unit_repo,
):
    path = selected_unit_repo / "new-file.txt"
    path.write_text("new content\n")
    _git("add", "--intent-to-add", path.name)
    line_changes = render_file_as_single_hunk(path.name)
    assert line_changes is not None

    with acquire_selected_fixup_unit(
        line_changes,
        None,
        source_tree=tree_for_commit("HEAD"),
    ) as unit:
        assert unit.kind == "text-file-addition"
        assert unit.unsupported_reason == "whole-file-addition"
        assert unit.patch_buffer is None
