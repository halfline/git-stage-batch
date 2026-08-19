"""Tests for explicit batch-source absence boundaries."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.core.coordinates import LineBoundary


def test_legacy_absence_anchor_zero_cannot_alias_sof():
    """Only ``None`` means SOF in the compatibility representation."""
    with pytest.raises(ValueError, match="positive"):
        AbsenceClaim(anchor_line=0, content_lines=(b"old\n",))


@pytest.mark.parametrize("anchor_line", [True, 1.5, "1"])
def test_legacy_absence_anchor_requires_an_integer_line(anchor_line: object):
    with pytest.raises(ValueError, match="positive"):
        AbsenceClaim(
            anchor_line=anchor_line,  # type: ignore[arg-type]
            content_lines=(b"old\n",),
        )


def test_explicit_sof_boundary_round_trips_to_legacy_none():
    claim = AbsenceClaim(
        anchor=LineBoundary(0),
        content_lines=(b"old\n",),
    )

    assert claim.anchor.offset == 0
    assert claim.anchor_line is None
