"""Focused tests for batch ownership model queries."""

from git_stage_batch.batch.ownership.claims import PresenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference


def test_presence_baseline_reference_uses_last_claim() -> None:
    """Single-reference lookup should preserve flattened-map precedence."""
    first_reference = BaselineReference(after_line=1)
    last_reference = BaselineReference(after_line=2)
    ownership = BatchOwnership(
        presence_claims=[
            PresenceClaim(
                source_lines=["1"],
                baseline_references={1: first_reference},
            ),
            PresenceClaim(
                source_lines=["1"],
                baseline_references={1: last_reference},
            ),
        ],
        deletions=[],
    )

    assert ownership.presence_baseline_reference(1) is last_reference
    assert ownership.presence_baseline_reference(2) is None
