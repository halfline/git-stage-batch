"""Tests for translating selection references onto batch baselines."""

from git_stage_batch.batch.merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
)
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
