"""Tests for batch comparison helpers."""

import pytest

from git_stage_batch.batch.comparison import (
    SemanticChangeKind,
    SemanticChangeRun,
    derive_replacement_display_id_run_sets_from_lines,
    derive_semantic_change_runs,
)
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange


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
    assert runs[0].source_line_numbers() == range(2, 4)
    assert runs[0].target_line_numbers() == range(2, 4)


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


def test_replacement_display_id_run_sets_accepts_non_list_sequences(line_sequence):
    """Replacement display grouping accepts indexed byte-line sequences."""
    line_changes = LineLevelChange(
        path="module.py",
        header=HunkHeader(old_start=1, old_len=2, new_start=1, new_len=4),
        lines=[
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=1,
                new_line_number=1,
                text_bytes=b"keep\n",
                text="keep\n",
            ),
            LineEntry(
                id=1,
                kind="-",
                old_line_number=2,
                new_line_number=None,
                text_bytes=b"old value\n",
                text="old value\n",
            ),
            LineEntry(
                id=2,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"new value 1\n",
                text="new value 1\n",
            ),
            LineEntry(
                id=3,
                kind="+",
                old_line_number=None,
                new_line_number=3,
                text_bytes=b"new value 2\n",
                text="new value 2\n",
            ),
            LineEntry(
                id=4,
                kind="+",
                old_line_number=None,
                new_line_number=4,
                text_bytes=b"new value 3\n",
                text="new value 3\n",
            ),
        ],
    )

    run_sets = derive_replacement_display_id_run_sets_from_lines(
        line_changes,
        source_lines=line_sequence([b"keep\n", b"old value\n"]),
        target_lines=line_sequence([
            b"keep\n",
            b"new value 1\n",
            b"new value 2\n",
            b"new value 3\n",
        ]),
    )

    assert run_sets == [{1, 2, 3, 4}]
