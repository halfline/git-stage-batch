"""Tests for translating selection references onto batch baselines."""

from git_stage_batch.batch.merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference


def test_translates_presence_gap_from_shifted_selection_baseline():
    source = [b"staged\n", b"a\n", b"b\n", b"b\n", b"b\n", b"b\n"]
    target = [b"a\n", b"b\n", b"b\n", b"b\n", b"b\n"]
    ownership = BatchOwnership.from_presence_lines(
        ["6"],
        baseline_references={
            6: BaselineReference(
                after_line=4,
                after_content=b"b",
                before_line=5,
                before_content=b"b",
                has_before_line=True,
            )
        },
    )

    translate_ownership_baseline_references(ownership, source, target)

    reference = ownership.presence_baseline_references()[6]
    assert reference.after_line == 3
    assert reference.before_line == 4


def test_translates_presence_references_from_mapped_record_order():
    """Reference projection sorts mapped records without relying on dict order."""
    source = [b"staged\n", b"a\n", b"b\n", b"c\n", b"d\n"]
    target = [b"a\n", b"b\n", b"c\n", b"d\n"]
    ownership = BatchOwnership.from_presence_lines(
        ["2,4"],
        baseline_references={
            4: BaselineReference(
                after_line=4,
                after_content=b"c",
                before_line=5,
                before_content=b"d",
                has_before_line=True,
            ),
            2: BaselineReference(
                after_line=2,
                after_content=b"a",
                before_line=3,
                before_content=b"b",
                has_before_line=True,
            ),
        },
    )

    translate_ownership_baseline_references(ownership, source, target)

    references = ownership.presence_baseline_references()
    assert references[2].after_line == 1
    assert references[2].before_line == 2
    assert references[4].after_line == 3
    assert references[4].before_line == 4


def test_translates_deletion_range_from_shifted_selection_baseline():
    source = [b"staged-one\n", b"staged-two\n", b"a\n", b"b\n", b"a\n", b"b\n"]
    target = [b"a\n", b"b\n", b"a\n", b"b\n"]
    deletion = AbsenceClaim(
        anchor_line=3,
        content_lines=[b"b\n"],
        baseline_reference=BaselineReference(
            after_line=3,
            after_content=b"a",
            before_line=5,
            before_content=b"a",
            has_before_line=True,
        ),
    )
    ownership = BatchOwnership.from_presence_lines([], [deletion])

    translate_ownership_baseline_references(ownership, source, target)

    reference = deletion.baseline_reference
    assert reference is not None
    assert reference.after_line == 1
    assert reference.before_line == 3
    assert reference.after_content == b"a\n"
    assert reference.before_content == b"a\n"


def test_translates_leading_deletion_past_target_prefix():
    source = [b"old\n", b"tail\n"]
    target = [b"prefix\n", b"old\n", b"tail\n"]
    deletion = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old\n"],
        baseline_reference=BaselineReference(
            after_line=None,
            after_content=None,
            has_after_line=True,
            before_line=2,
            before_content=b"tail",
            has_before_line=True,
        ),
    )
    ownership = BatchOwnership.from_presence_lines([], [deletion])

    translate_ownership_baseline_references(ownership, source, target)

    reference = deletion.baseline_reference
    assert reference is not None
    assert reference.after_line == 1
    assert reference.before_line == 3
    assert reference.after_content == b"prefix\n"
    assert reference.before_content == b"tail\n"


def test_translates_trailing_deletion_before_target_suffix():
    source = [b"head\n", b"old\n"]
    target = [b"head\n", b"old\n", b"suffix\n"]
    deletion = AbsenceClaim(
        anchor_line=1,
        content_lines=[b"old\n"],
        baseline_reference=BaselineReference(
            after_line=1,
            after_content=b"head",
            has_after_line=True,
        ),
    )
    ownership = BatchOwnership.from_presence_lines([], [deletion])

    translate_ownership_baseline_references(ownership, source, target)

    reference = deletion.baseline_reference
    assert reference is not None
    assert reference.after_line == 1
    assert reference.before_line == 3
    assert reference.after_content == b"head\n"
    assert reference.before_content == b"suffix\n"
