"""Tests for explicit baseline-boundary evidence variants."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.ownership.references import (
    BaselineReference,
    BoundBoundary,
    KnownBoundary,
    UnknownBoundary,
)
from git_stage_batch.core.coordinates import (
    BaselineSpace,
    FileSnapshot,
    SnapshotIdentity,
)


def test_known_boundary_rejects_content_without_a_line():
    """SOF/EOF is boundary evidence, not a synthetic content-bearing line."""
    with pytest.raises(ValueError, match="cannot carry"):
        KnownBoundary(None, b"not-a-line")


@pytest.mark.parametrize("line", [True, 1.5, "1"])
def test_known_boundary_rejects_noninteger_line_coordinates(line: object):
    with pytest.raises(ValueError, match="positive"):
        KnownBoundary(line)  # type: ignore[arg-type]


def test_baseline_reference_rejects_mixed_variant_and_legacy_fields():
    with pytest.raises(ValueError, match="after evidence"):
        BaselineReference(
            after_line=1,
            after=KnownBoundary(1, b"line\n"),
        )
    with pytest.raises(ValueError, match="before evidence"):
        BaselineReference(
            before_line=2,
            has_before_line=True,
            before=KnownBoundary(2, b"line\n"),
        )


def test_baseline_reference_accepts_canonical_variants():
    reference = BaselineReference(
        after=UnknownBoundary(),
        before=KnownBoundary(2, b"line\n"),
    )

    assert not reference.has_after_line
    assert reference.before_line == 2


def test_baseline_reference_binding_distinguishes_unknown_sof_and_eof():
    """Both file edges become explicit positions while absence stays unknown."""
    snapshot = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "baseline"),
        3,
        BaselineSpace,
    )

    edges = BaselineReference(
        after=KnownBoundary(None),
        before=KnownBoundary(None),
    ).bind(snapshot)
    unknown = BaselineReference(
        after=UnknownBoundary(),
        before=UnknownBoundary(),
    ).bind(snapshot)

    assert isinstance(edges.after, BoundBoundary)
    assert edges.after.position.boundary.offset == 0
    assert isinstance(edges.before, BoundBoundary)
    assert edges.before.position.boundary.offset == 3
    assert isinstance(unknown.after, UnknownBoundary)
    assert isinstance(unknown.before, UnknownBoundary)


def test_baseline_reference_binding_rejects_foreign_geometry():
    snapshot = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "baseline"),
        1,
        BaselineSpace,
    )

    with pytest.raises(ValueError, match="after-boundary"):
        BaselineReference(after=KnownBoundary(2)).bind(snapshot)
    with pytest.raises(ValueError, match="before-boundary"):
        BaselineReference(before=KnownBoundary(3)).bind(snapshot)
