"""Tests for range-compressed fixup lineage evidence."""

from __future__ import annotations

import gc
import tracemalloc

from git_stage_batch.fixup import lineage as lineage_module
from git_stage_batch.fixup.lineage import (
    _incremental_header,
    analyze_lineage,
    analyze_lineage_history,
)
from git_stage_batch.fixup.models import FixupRange, FixupUnit


def _replacement_unit(line_count: int) -> FixupUnit:
    return FixupUnit(
        unit_id="unit",
        path="file.txt",
        kind="text-replacement",
        patch_buffer=None,
        old_start=1,
        old_len=line_count,
        new_start=1,
        new_len=line_count,
    )


def _incremental_blame_for(commit: str):
    commit_bytes = commit.encode("ascii")

    def stream(arguments, *_args, **_kwargs):
        range_spec = arguments[arguments.index("-L") + 1]
        start_text, end_text = range_spec.split(",", 1)
        start = int(start_text)
        end = int(end_text)
        for line_number in range(start, end + 1):
            yield (commit_bytes + f" {line_number} {line_number} 1\n".encode("ascii"))

    return stream


def test_lineage_avoids_line_scale_python_heap(monkeypatch):
    """Incremental blame is reduced to scalar counters while it streams."""
    commit = "1" * 40
    commit_range = FixupRange(
        object_format="sha1",
        base_commit="0" * 40,
        head_commit=commit,
        commits_newest_first=(commit,),
    )
    monkeypatch.setattr(
        lineage_module,
        "stream_git_command",
        _incremental_blame_for(commit),
    )
    monkeypatch.setattr(lineage_module, "object_id_hex_length", lambda: 40)
    heap_peaks: list[int] = []

    for line_count in (1024, 65536):
        unit = _replacement_unit(line_count)
        gc.collect()
        tracemalloc.start()
        try:
            evidence = analyze_lineage(unit, commit_range)
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert evidence.queried_ranges == ((1, line_count),)
        assert evidence.queried_line_count == line_count
        assert evidence.resolved_line_count == line_count
        assert evidence.in_range_line_count == line_count
        assert evidence.candidates == (commit,)
        assert evidence.conclusive is True
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 64 * 1024


def test_lineage_retains_only_two_ambiguity_witnesses(monkeypatch):
    commits = ("1" * 40, "2" * 40, "3" * 40)

    def stream(_arguments, *_args, **_kwargs):
        for line_number, commit in enumerate(commits, start=1):
            yield f"{commit} {line_number} {line_number} 1\n".encode("ascii")

    monkeypatch.setattr(lineage_module, "stream_git_command", stream)
    monkeypatch.setattr(lineage_module, "object_id_hex_length", lambda: 40)
    evidence = analyze_lineage(
        _replacement_unit(3),
        FixupRange(
            object_format="sha1",
            base_commit="0" * 40,
            head_commit=commits[-1],
            commits_newest_first=tuple(reversed(commits)),
        ),
    )

    assert len(evidence.candidates) == 2
    assert evidence.conclusive is False


def test_lineage_neutralizes_configured_ignored_revisions(monkeypatch):
    """Repository blame policy must not alter fixup attribution."""
    commit = "1" * 40
    captured_arguments: list[list[str]] = []

    def stream(arguments, *_args, **_kwargs):
        captured_arguments.append(arguments)
        yield f"{commit} 1 1 1\n".encode("ascii")

    monkeypatch.setattr(lineage_module, "stream_git_command", stream)
    monkeypatch.setattr(lineage_module, "object_id_hex_length", lambda: 40)

    evidence = analyze_lineage(
        _replacement_unit(1),
        FixupRange(
            object_format="sha1",
            base_commit="0" * 40,
            head_commit=commit,
            commits_newest_first=(commit,),
        ),
    )

    assert evidence.unique_target == commit
    assert len(captured_arguments) == 1
    assert "--root" in captured_arguments[0]
    assert "--no-ignore-revs-file" in captured_arguments[0]


def test_incremental_metadata_does_not_trigger_word_scale_splitting():
    class SplitGuard(bytes):
        def split(self, *_args, **_kwargs):
            raise AssertionError("metadata lines must not be split")

    metadata = SplitGuard(b"summary " + (b"word " * 65536))

    assert _incremental_header(metadata, object_id_width=40) is None


def test_history_searches_disjoint_ranges_independently(monkeypatch):
    newer = "2" * 40
    older = "1" * 40
    calls: list[tuple[list[str], dict[str, object]]] = []

    def stream(arguments, *_args, **kwargs):
        calls.append((arguments, kwargs))
        range_spec = arguments[arguments.index("-L") + 1]
        yield ((newer if range_spec.startswith("3,3:") else older) + "\n").encode()

    monkeypatch.setattr(lineage_module, "stream_git_command", stream)
    monkeypatch.setattr(lineage_module, "object_id_hex_length", lambda: 40)
    evidence = analyze_lineage_history(
        FixupUnit(
            unit_id="unit",
            path=":(top)literal",
            kind="text-replacement",
            patch_buffer=None,
            lineage_ranges=((1, 1), (3, 3)),
        ),
        FixupRange(
            object_format="sha1",
            base_commit="0" * 40,
            head_commit=newer,
            commits_newest_first=(newer, older),
        ),
    )

    assert evidence.queried_ranges == ((1, 1), (3, 3))
    assert evidence.candidates == (newer, older)
    assert evidence.completed_range_count == 2
    assert evidence.complete is True
    assert [
        arguments[arguments.index("-L") + 1]
        for arguments, _kwargs in calls
    ] == ["1,1::(top)literal", "3,3::(top)literal"]
    assert all(kwargs["literal_pathspecs"] is True for _arguments, kwargs in calls)
