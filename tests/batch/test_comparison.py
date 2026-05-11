"""Tests for batch comparison helpers."""

from git_stage_batch.batch.comparison import (
    SemanticChangeKind,
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
    assert runs[0].source_run == [2]
    assert runs[0].target_run == [2]
    assert runs[0].target_anchor == 1


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
