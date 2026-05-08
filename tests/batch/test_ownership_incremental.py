"""Test for batch ownership with constraint-based deletion model."""

from __future__ import annotations

from git_stage_batch.batch.ownership import (
    DeletionClaim,
    ReplacementUnit,
    translate_lines_to_batch_ownership,
)
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
