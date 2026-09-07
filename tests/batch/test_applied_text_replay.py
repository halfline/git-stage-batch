"""Checks for composing saved paragraph prefixes with applied ownership."""

import pytest

from git_stage_batch.batch.applied_text_replay import _compose_overlapping_presence_prefix

from git_stage_batch.batch.applied_text_replay import AppliedTextApplication, _AcquiredTextApplication
from git_stage_batch.batch.ownership.model import BatchOwnership



_REPLACEMENT = (
    b"Heading\n",
    b"Shared paragraph context\n",
    b"capture ownership and revocation including\n",
    b"creator-close and final-holder cleanup.\n",
    b"\n",
    b"Next heading\n",
)
_CHANGED = (
    *_REPLACEMENT[:2],
    b"capture ownership grant `fdinfo`, and revocation including\n",
    *_REPLACEMENT[4:],
)


def _application(source, presence):
    record = AppliedTextApplication(
        batch_name="ownership",
        file_path="contract.md",
        baseline_commit=None,
        source_object_id="unused",
        file_metadata={},
    )
    return _AcquiredTextApplication(
        record,
        source,
        BatchOwnership.from_presence_lines(presence),
        (),
    )


def test_compose_prefix_preserves_owned_tail(tmp_path):
    """Added words retain the applied run's remaining whole lines."""
    result = _compose_overlapping_presence_prefix(
        _CHANGED,
        _REPLACEMENT,
        _application(_REPLACEMENT, ["2-4"]),
        spool_dir=tmp_path,
    )
    assert result is not None
    with result:
        assert b"".join(result) == b"".join((*_CHANGED[:3], *_REPLACEMENT[3:]))


@pytest.mark.parametrize(
    "changed, replacement, source, presence",
    [
        pytest.param(
            (*_REPLACEMENT[:3], *_REPLACEMENT[4:]),
            _REPLACEMENT,
            _REPLACEMENT,
            ["2-4"],
            id="deletion-without-added-words",
        ),
        pytest.param(
            (*_CHANGED[:2], b"capture ownership grant and revocation\n", *_CHANGED[3:]),
            _REPLACEMENT,
            _REPLACEMENT,
            ["2-4"],
            id="partial-line-prefix",
        ),
    ],
)
def test_compose_prefix_refuses_unproved_overlap(
    tmp_path, changed, replacement, source, presence
):
    """A partial match cannot overwrite conflicts or claim unrelated text."""
    result = _compose_overlapping_presence_prefix(
        changed,
        replacement,
        _application(source, presence),
        spool_dir=tmp_path,
    )
    if result is not None:
        result.close()
    assert result is None
