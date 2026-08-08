"""Tests for bounded tree-commutation patch storage."""

from __future__ import annotations

import gc
import subprocess
import tracemalloc

import pytest

from git_stage_batch.fixup import models as fixup_models
from git_stage_batch.fixup.commutation import (
    analyze_placement,
    load_tree_diff_as_buffer,
)
from git_stage_batch.fixup.models import FixupRange


_FIXUP_UNIT_TYPE = getattr(fixup_models, "FixupUnit", None) or getattr(
    fixup_models, "StagedFixupUnit"
)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def tree_diff_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    (tmp_path / "anchor.txt").write_text("anchor\n")
    _git("add", "anchor.txt")
    _git("commit", "-m", "Base")
    return tmp_path, _git("rev-parse", "HEAD^{tree}")


def test_tree_diffs_avoid_line_scale_python_heap(tree_diff_repo):
    """Generated relocation patches spill to mmap-capable buffers."""
    repo, base_tree = tree_diff_repo
    line = b"y" * 511 + b"\n"
    heap_peaks: list[int] = []

    for line_count in (4096, 32768):
        path = repo / f"large-{line_count}.txt"
        path.write_bytes(line * line_count)
        _git("add", "--", path.name)
        target_tree = _git("write-tree")

        gc.collect()
        tracemalloc.start()
        try:
            with load_tree_diff_as_buffer(base_tree, target_tree) as patch_buffer:
                assert patch_buffer.byte_count > len(line) * line_count
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)

        _git("read-tree", base_tree)
        path.unlink()

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 64 * 1024


def test_patch_commutes_across_empty_commit(tree_diff_repo):
    """An empty commit has no replay patch and cannot be a false barrier."""
    repo, head_tree = tree_diff_repo
    base = _git("rev-parse", "HEAD")
    _git("commit", "--allow-empty", "-m", "Empty")
    head = _git("rev-parse", "HEAD")
    (repo / "anchor.txt").write_text("changed\n")
    _git("add", "anchor.txt")
    changed_tree = _git("write-tree")

    with load_tree_diff_as_buffer(head_tree, changed_tree) as patch_buffer:
        unit = _FIXUP_UNIT_TYPE(
            unit_id="1" * 64,
            path="anchor.txt",
            kind="text-replacement",
            patch_buffer=patch_buffer,
        )
        placement = analyze_placement(
            unit,
            FixupRange(
                base_commit=base,
                head_commit=head,
                commits_newest_first=(head,),
            ),
        )

    assert placement.status == "commutes-through"
    assert placement.barrier is None
    assert placement.commuted_across == (head,)
