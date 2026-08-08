"""Tests for bounded history-plan tree replay."""

from __future__ import annotations

import gc
import tracemalloc

from git_stage_batch.history.replay import materialize_history_output_trees
from git_stage_batch.history.scan import acquire_history_plan_document

from .conftest import git


def test_history_replay_avoids_line_scale_python_heap(tmp_path, monkeypatch):
    """Full source patches remain spill-backed while output trees are built."""
    line = b"history payload " + b"x" * 496 + b"\n"
    heap_peaks: list[int] = []

    for line_count in (4096, 32768):
        repository = tmp_path / f"repo-{line_count}"
        repository.mkdir()
        monkeypatch.chdir(repository)
        git("init", "-q", "-b", "topic")
        git("config", "user.name", "Test User")
        git("config", "user.email", "test@example.com")
        (repository / "anchor.txt").write_text("anchor\n", encoding="utf-8")
        git("add", "anchor.txt")
        git("commit", "-m", "Base")
        base = git("rev-parse", "HEAD")
        large_path = repository / "large.txt"
        large_path.write_bytes(line * line_count)
        git("add", "large.txt")
        git("commit", "-m", "Add large history payload")
        document = acquire_history_plan_document(base)

        gc.collect()
        tracemalloc.start()
        try:
            replay = materialize_history_output_trees(document)
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)
        assert replay.final_tree == document.snapshot.final_tree

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 64 * 1024
