"""Test for batch ownership with constraint-based deletion model."""

from __future__ import annotations

from git_stage_batch.batch.ownership import (
    DeletionClaim,
    ReplacementLineRun,
    ReplacementUnit,
    derive_replacement_line_runs_from_lines,
    translate_hunk_selection_to_batch_ownership,
    translate_lines_to_batch_ownership,
)
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.core.models import LineEntry


def test_translate_lines_creates_deletion_constraints():
    """Test that deletions become suppression constraints, not content to replay."""
    lines = [
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                  text_bytes=b'old_version', text='old_version', source_line=None),
        LineEntry(id=2, kind='+', old_line_number=None, new_line_number=1,
                  text_bytes=b'new_version', text='new_version', source_line=1),
    ]

    ownership = translate_lines_to_batch_ownership(lines)

    # Should claim the + line (presence claim)
    assert '1' in ','.join(ownership.presence_claims[0].source_lines)

    # Should create deletion constraint for - line (suppression constraint)
    assert len(ownership.deletions) == 1
    assert isinstance(ownership.deletions[0], DeletionClaim)
    assert ownership.deletions[0].content_lines == [b'old_version\n']
    assert len(ownership.replacement_units) == 1
    assert ownership.replacement_units[0].presence_lines == ["1"]
    assert ownership.replacement_units[0].deletion_indices == [0]


def test_presence_line_set_returns_line_ranges():
    """Ownership presence lines stay range-backed after translation."""
    lines = [
        LineEntry(id=1, kind='+', old_line_number=None, new_line_number=1,
                  text_bytes=b'one', text='one', source_line=1),
        LineEntry(id=2, kind='+', old_line_number=None, new_line_number=2,
                  text_bytes=b'two', text='two', source_line=2),
        LineEntry(id=3, kind='+', old_line_number=None, new_line_number=4,
                  text_bytes=b'four', text='four', source_line=4),
    ]

    ownership = translate_lines_to_batch_ownership(lines)
    selection = ownership.presence_line_set()

    assert isinstance(selection, LineRanges)
    assert selection.ranges() == ((1, 2), (4, 4))
    assert ownership.resolve().presence_line_set == selection


def test_derive_replacement_line_runs_accepts_non_list_sequences(line_sequence):
    """Replacement run derivation accepts indexed byte-line sequences."""
    runs = derive_replacement_line_runs_from_lines(
        old_file_lines=line_sequence([b"keep\n", b"old value\n"]),
        new_file_lines=line_sequence([
            b"keep\n",
            b"new value 1\n",
            b"new value 2\n",
            b"new value 3\n",
        ]),
    )

    assert len(runs) == 1
    assert (runs[0].old_start, runs[0].old_end) == (2, 2)
    assert (runs[0].new_start, runs[0].new_end) == (2, 4)
    assert runs[0].old_line_numbers() == range(2, 3)
    assert runs[0].new_line_numbers() == range(2, 5)


def test_derive_replacement_line_runs_keeps_large_replacements_compact():
    """Large replacement runs should stay as endpoints."""
    runs = derive_replacement_line_runs_from_lines(
        old_file_lines=[
            b"head\n",
            *[f"old {index}\n".encode() for index in range(1000)],
            b"tail\n",
        ],
        new_file_lines=[
            b"head\n",
            *[f"new {index}\n".encode() for index in range(1000)],
            b"tail\n",
        ],
    )

    assert runs == [
        ReplacementLineRun(
            old_start=2,
            old_end=1001,
            new_start=2,
            new_end=1001,
        )
    ]


def test_translate_lines_records_multi_line_replacement_unit():
    """Captured multi-line replacements should remain one atomic unit."""
    lines = [
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                  text_bytes=b'old one', text='old one', source_line=None),
        LineEntry(id=2, kind='-', old_line_number=2, new_line_number=None,
                  text_bytes=b'old two', text='old two', source_line=None),
        LineEntry(id=3, kind='+', old_line_number=None, new_line_number=1,
                  text_bytes=b'new one', text='new one', source_line=1),
        LineEntry(id=4, kind='+', old_line_number=None, new_line_number=2,
                  text_bytes=b'new two', text='new two', source_line=2),
    ]

    ownership = translate_lines_to_batch_ownership(lines)

    assert ownership.replacement_units == [
        ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
    ]


def test_translate_lines_preserves_deletion_structure():
    """Test that each deletion run becomes a separate claim."""
    lines = [
        # First deletion run
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                  text_bytes=b'del1', text='del1', source_line=None),
        LineEntry(id=2, kind='-', old_line_number=2, new_line_number=None,
                  text_bytes=b'del2', text='del2', source_line=None),
        # Context line
        LineEntry(id=3, kind=' ', old_line_number=3, new_line_number=1,
                  text_bytes=b'context', text='context', source_line=1),
        # Second deletion run
        LineEntry(id=4, kind='-', old_line_number=4, new_line_number=None,
                  text_bytes=b'del3', text='del3', source_line=1),
    ]

    ownership = translate_lines_to_batch_ownership(lines)

    # Should have two separate deletion claims (not collapsed)
    assert len(ownership.deletions) == 2
    assert ownership.deletions[0].content_lines == [b'del1\n', b'del2\n']
    assert ownership.deletions[0].anchor_line is None  # before any source line
    assert ownership.deletions[1].content_lines == [b'del3\n']
    assert ownership.deletions[1].anchor_line == 1  # after source line 1
    assert ownership.replacement_units == []


def test_translate_lines_keeps_file_start_anchor_for_deletion_run():
    """A file-start deletion run should keep its None anchor."""
    lines = [
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                  text_bytes=b'first', text='first', source_line=None),
        LineEntry(id=2, kind='-', old_line_number=2, new_line_number=None,
                  text_bytes=b'second', text='second', source_line=1),
    ]

    ownership = translate_lines_to_batch_ownership(lines)

    assert len(ownership.deletions) == 1
    assert ownership.deletions[0].anchor_line is None
    assert ownership.deletions[0].content_lines == [b'first\n', b'second\n']


def test_translate_hunk_selection_uses_full_hunk_boundaries():
    """Unselected rows should delimit selected claims without being owned."""
    lines = [
        LineEntry(id=1, kind='+', old_line_number=None, new_line_number=1,
                  text_bytes=b'x', text='x', source_line=1,
                  has_baseline_reference_after=True,
                  baseline_reference_after_line=None,
                  has_baseline_reference_before=True,
                  baseline_reference_before_line=1,
                  baseline_reference_before_text_bytes=b'same'),
        LineEntry(id=2, kind='+', old_line_number=None, new_line_number=2,
                  text_bytes=b'same', text='same', source_line=2),
        LineEntry(id=None, kind=' ', old_line_number=1, new_line_number=3,
                  text_bytes=b'same', text='same', source_line=3),
        LineEntry(id=4, kind='-', old_line_number=2, new_line_number=None,
                  text_bytes=b'a', text='a', source_line=3),
        LineEntry(id=None, kind=' ', old_line_number=3, new_line_number=4,
                  text_bytes=b'c', text='c', source_line=4),
    ]

    ownership = translate_hunk_selection_to_batch_ownership(lines, {1, 4})

    assert ownership.presence_line_set() == {1}
    assert len(ownership.deletions) == 1
    assert ownership.deletions[0].content_lines == [b'a\n']
    assert ownership.deletions[0].baseline_reference.after_line == 1
    assert ownership.deletions[0].baseline_reference.after_content == b'same'
    assert ownership.deletions[0].baseline_reference.before_line == 3
    assert ownership.deletions[0].baseline_reference.before_content == b'c'
    assert ownership.replacement_units == []


def test_translate_hunk_selection_uses_file_derived_replacement_runs():
    """Replacement units come from caller-provided before/after line runs."""
    lines = [
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                  text_bytes=b'a', text='a', source_line=None),
        LineEntry(id=2, kind='-', old_line_number=2, new_line_number=None,
                  text_bytes=b'b', text='b', source_line=1),
        LineEntry(id=3, kind='+', old_line_number=None, new_line_number=1,
                  text_bytes=b'A', text='A', source_line=1,
                  has_baseline_reference_after=True,
                  baseline_reference_after_line=2,
                  baseline_reference_after_text_bytes=b'b'),
        LineEntry(id=4, kind='+', old_line_number=None, new_line_number=2,
                  text_bytes=b'B', text='B', source_line=2),
    ]

    ownership = translate_hunk_selection_to_batch_ownership(
        lines,
        {1, 3},
        replacement_line_runs=[
            ReplacementLineRun(
                old_start=1,
                old_end=2,
                new_start=1,
                new_end=2,
            ),
        ],
    )

    assert ownership.presence_line_set() == {1}
    assert len(ownership.deletions) == 1
    assert ownership.deletions[0].content_lines == [b'a\n']
    assert ownership.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
    ]


def test_translate_hunk_selection_keeps_one_to_many_replacement_atomic():
    """A 1-to-N replacement run should be one unit, not invented line pairs."""
    lines = [
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                  text_bytes=b'old', text='old', source_line=None),
        LineEntry(id=2, kind='+', old_line_number=None, new_line_number=1,
                  text_bytes=b'new one', text='new one', source_line=1),
        LineEntry(id=3, kind='+', old_line_number=None, new_line_number=2,
                  text_bytes=b'new two', text='new two', source_line=2),
    ]

    ownership = translate_hunk_selection_to_batch_ownership(
        lines,
        {1, 2, 3},
        replacement_line_runs=[
            ReplacementLineRun(
                old_start=1,
                old_end=1,
                new_start=1,
                new_end=2,
            ),
        ],
    )

    assert ownership.presence_line_set() == {1, 2}
    assert len(ownership.deletions) == 1
    assert ownership.deletions[0].content_lines == [b'old\n']
    assert ownership.replacement_units == [
        ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
    ]
