"""Tests for batch comparison helpers."""

import gc
from itertools import product
import tracemalloc

import pytest

import git_stage_batch.batch.line_matching.comparison as comparison_module
from git_stage_batch.batch.line_matching.comparison import (
    SemanticChangeKind,
    SemanticChangeRun,
    derive_semantic_change_runs,
    stream_semantic_change_runs,
)
from git_stage_batch.batch.line_matching.match import match_lines


_LINE_SCALE_HEAP_LIMIT = 256 * 1024


def test_derive_semantic_change_runs_accepts_non_list_sequences(line_sequence):
    """Semantic comparison only requires sized indexable line sequences."""
    source = line_sequence([b"line1\n", b"old\n", b"line3\n"])
    target = line_sequence([b"line1\n", b"new\n", b"line3\n"])

    runs = derive_semantic_change_runs(source, target)

    assert len(runs) == 1
    assert runs[0].kind == SemanticChangeKind.REPLACEMENT
    assert (runs[0].source_start, runs[0].source_end) == (2, 2)
    assert (runs[0].target_start, runs[0].target_end) == (2, 2)
    assert runs[0].target_anchor == 1


def test_trusted_matching_skips_reciprocal_pass_for_disjoint_gaps(monkeypatch):
    """A forward alignment is final when its gaps share no equal content."""
    calls = 0
    real_match_lines = comparison_module.match_lines

    def count_match(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_match_lines(*args, **kwargs)

    monkeypatch.setattr(comparison_module, "match_lines", count_match)

    runs = derive_semantic_change_runs(
        [b"head\n", b"old\n", b"tail\n"],
        [b"head\n", b"new\n", b"tail\n"],
    )

    assert calls == 1
    assert runs == [
        SemanticChangeRun(
            kind=SemanticChangeKind.REPLACEMENT,
            source_start=2,
            source_end=2,
            target_start=2,
            target_end=2,
            target_anchor=1,
        )
    ]


def test_trusted_matching_keeps_reciprocal_pass_for_crossed_lines(monkeypatch):
    """Direction-dependent crossed matches must still be rejected."""
    calls = 0
    real_match_lines = comparison_module.match_lines

    def count_match(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_match_lines(*args, **kwargs)

    monkeypatch.setattr(comparison_module, "match_lines", count_match)

    runs = derive_semantic_change_runs(
        [b"first\n", b"second\n"],
        [b"second\n", b"first\n"],
    )

    assert calls == 2
    assert runs == [
        SemanticChangeRun(
            kind=SemanticChangeKind.REPLACEMENT,
            source_start=1,
            source_end=2,
            target_start=1,
            target_end=2,
        )
    ]


def test_trusted_matching_fast_path_matches_bidirectional_reference():
    """Fast-path trust must equal an explicit reciprocal intersection."""
    alphabet = (b"a\n", b"b\n")
    sequences = [
        values
        for length in range(4)
        for values in product(alphabet, repeat=length)
    ]

    for source in sequences:
        for target in sequences:
            with (
                match_lines(source, target) as forward,
                match_lines(target, source) as reverse,
            ):
                reverse_pairs = {
                    (source_line, target_line)
                    for target_line, source_line in reverse.mapped_line_pairs()
                }
                expected = tuple(
                    pair
                    for pair in forward.mapped_line_pairs()
                    if pair in reverse_pairs
                )

            assert tuple(comparison_module._trusted_matched_pairs(
                source,
                target,
            )) == expected


def test_large_unmapped_disjoint_gaps_avoid_line_scale_python_heap():
    """Large reciprocal checks should index gaps in mapped storage."""
    line_count = 8192
    midpoint = line_count // 2
    source = [f"old-{index}\n".encode() for index in range(line_count)]
    target = [f"new-{index}\n".encode() for index in range(line_count)]
    source.insert(midpoint, b"shared anchor\n")
    target.insert(midpoint, b"shared anchor\n")

    gc.collect()
    tracemalloc.start()
    try:
        runs = derive_semantic_change_runs(source, target)
        _current_heap, peak_heap = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(runs) == 2
    assert peak_heap < _LINE_SCALE_HEAP_LIMIT


def test_stream_semantic_change_runs_closes_matching_pairs(monkeypatch):
    """Closing streamed changes must release their matcher resources."""

    class ClosablePairs:
        def __init__(self):
            self._pairs = iter(((2, 2),))
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._pairs)

        def close(self):
            self.closed = True

    matched_pairs = ClosablePairs()
    monkeypatch.setattr(
        comparison_module,
        "_trusted_matched_pairs",
        lambda *_args, **_kwargs: matched_pairs,
    )
    runs = stream_semantic_change_runs(
        [b"old\n", b"tail\n"],
        [b"new\n", b"tail\n"],
    )

    assert next(runs).kind == SemanticChangeKind.REPLACEMENT
    assert matched_pairs.closed is False

    runs.close()

    assert matched_pairs.closed is True


def test_derive_semantic_change_runs_uses_range_records():
    """Semantic comparison should store line runs as endpoints."""
    source = [b"keep\n", b"old one\n", b"old two\n", b"tail\n"]
    target = [b"keep\n", b"new one\n", b"new two\n", b"tail\n"]

    runs = derive_semantic_change_runs(source, target)

    assert runs == [
        SemanticChangeRun(
            kind=SemanticChangeKind.REPLACEMENT,
            source_start=2,
            source_end=3,
            target_start=2,
            target_end=3,
            target_anchor=1,
        )
    ]
    assert not hasattr(runs[0], "source_run")
    assert not hasattr(runs[0], "target_run")


def test_semantic_change_run_rejects_invalid_ranges():
    """Range records should not accept partial or inverted endpoints."""
    with pytest.raises(ValueError, match="source range requires both"):
        SemanticChangeRun(
            kind=SemanticChangeKind.DELETION,
            source_start=2,
        )

    with pytest.raises(ValueError, match="target range start must be <= end"):
        SemanticChangeRun(
            kind=SemanticChangeKind.PRESENCE,
            target_start=4,
            target_end=3,
        )


def test_derive_semantic_change_runs_keeps_large_replacements_compact():
    """Large contiguous replacements should remain one range record."""
    source = [
        b"head\n",
        *[f"old {index}\n".encode() for index in range(1000)],
        b"tail\n",
    ]
    target = [
        b"head\n",
        *[f"new {index}\n".encode() for index in range(1000)],
        b"tail\n",
    ]

    runs = derive_semantic_change_runs(source, target)

    assert len(runs) == 1
    assert runs[0].kind == SemanticChangeKind.REPLACEMENT
    assert (runs[0].source_start, runs[0].source_end) == (2, 1001)
    assert (runs[0].target_start, runs[0].target_end) == (2, 1001)
    assert runs[0].target_anchor == 1


def test_derive_semantic_change_runs_uses_ranges_for_one_sided_changes():
    """Pure additions and deletions should also use endpoints."""
    deletion_runs = derive_semantic_change_runs(
        [b"keep\n", b"old one\n", b"old two\n", b"tail\n"],
        [b"keep\n", b"tail\n"],
    )
    presence_runs = derive_semantic_change_runs(
        [b"keep\n", b"tail\n"],
        [b"keep\n", b"new one\n", b"new two\n", b"tail\n"],
    )

    assert deletion_runs == [
        SemanticChangeRun(
            kind=SemanticChangeKind.DELETION,
            source_start=2,
            source_end=3,
            target_anchor=1,
        )
    ]
    assert presence_runs == [
        SemanticChangeRun(
            kind=SemanticChangeKind.PRESENCE,
            target_start=2,
            target_end=3,
        )
    ]
