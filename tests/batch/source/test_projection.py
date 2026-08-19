"""Tests for immutable rendered-row source projections."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.source.projection import SourceCoordinateProjection
from git_stage_batch.core.coordinates import (
    DisplayLineId,
    FileSnapshot,
    BatchSourceSpace,
    SnapshotIdentity,
)
from git_stage_batch.core.models import LineEntry


def test_projection_overrides_selected_row_without_mutating_it():
    """Source refresh is an overlay rather than temporary row mutation."""
    line = LineEntry(2, "+", None, 1, text_bytes=b"new", source_line=7)
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        10,
        BatchSourceSpace,
    )

    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "view"),
        source_snapshot=source,
        pairs=((DisplayLineId(2), 3),),
        capacity=1,
    ) as projection:
        assert projection.source_line_for(line) == 3
        assert line.source_line == 7

    assert line.source_line == 7


@pytest.mark.parametrize(
    "view_identity",
    [
        SnapshotIdentity("test-view", "view"),
        SnapshotIdentity("content-sha256", "view"),
    ],
)
def test_projection_rejects_non_diff_view_identity(
    view_identity: SnapshotIdentity,
) -> None:
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        0,
        BatchSourceSpace,
    )

    with pytest.raises(ValueError, match="diff-view identity"):
        SourceCoordinateProjection.from_pairs(
            view_identity=view_identity,
            source_snapshot=source,
            pairs=(),
            capacity=0,
        )


def test_projection_rejects_coordinate_from_another_source_extent():
    """Plausible integers outside the bound source fail immediately."""
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        2,
        BatchSourceSpace,
    )

    with pytest.raises(ValueError, match="outside"):
        SourceCoordinateProjection.from_pairs(
            view_identity=SnapshotIdentity("diff-view-sha256", "view"),
            source_snapshot=source,
            pairs=((DisplayLineId(1), 3),),
            capacity=1,
        )


def test_projection_rejects_rows_from_another_rendered_view():
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        2,
        BatchSourceSpace,
    )
    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "first"),
        source_snapshot=source,
        pairs=((DisplayLineId(1), 1),),
        capacity=1,
    ) as projection:
        with pytest.raises(ValueError, match="another diff view"):
            projection.require_view(SnapshotIdentity("diff-view-sha256", "second"))


def test_projection_rejects_unprojected_display_row():
    """A stale rendered annotation cannot stand in for exact projection."""
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        2,
        BatchSourceSpace,
    )
    unprojected = LineEntry(
        2,
        "+",
        None,
        2,
        text_bytes=b"plausible",
        source_line=2,
    )

    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "view"),
        source_snapshot=source,
        pairs=((DisplayLineId(1), 1),),
        capacity=1,
    ) as projection:
        with pytest.raises(ValueError, match="absent from exact source projection"):
            projection.source_line_for(unprojected)


def test_projection_rejects_unidentified_rendered_row():
    """Rows without display IDs cannot be looked up in an ID projection."""
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        1,
        BatchSourceSpace,
    )
    context = LineEntry(
        None,
        " ",
        1,
        1,
        text_bytes=b"context",
        source_line=1,
    )

    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "view"),
        source_snapshot=source,
        pairs=(),
        capacity=0,
    ) as projection:
        with pytest.raises(ValueError, match="absent from exact source projection"):
            projection.source_line_for(context)


def test_projection_resolves_anonymous_row_with_explicit_authority():
    """An exact resolver may project context without trusting its annotation."""
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        3,
        BatchSourceSpace,
    )
    context = LineEntry(
        None,
        " ",
        2,
        2,
        text_bytes=b"context",
        source_line=3,
    )

    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "view"),
        source_snapshot=source,
        pairs=(),
        capacity=0,
        anonymous_row_resolver=lambda new_line_number: new_line_number,
    ) as projection:
        assert projection.source_line_for(context) == 2


def test_projection_rejects_invalid_anonymous_resolution():
    """Resolver authority remains bounded by the projection's exact source."""
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        1,
        BatchSourceSpace,
    )
    context = LineEntry(
        None,
        " ",
        1,
        1,
        text_bytes=b"context",
        source_line=1,
    )

    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "view"),
        source_snapshot=source,
        pairs=(),
        capacity=0,
        anonymous_row_resolver=lambda _new_line_number: 2,
    ) as projection:
        with pytest.raises(ValueError, match="outside snapshot"):
            projection.source_line_for(context)


def test_anonymous_resolver_cannot_rescue_missing_display_id():
    """Every identified row must still occupy an immutable projection record."""
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        1,
        BatchSourceSpace,
    )
    unprojected = LineEntry(
        2,
        "+",
        None,
        1,
        text_bytes=b"new",
        source_line=1,
    )

    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "view"),
        source_snapshot=source,
        pairs=((DisplayLineId(1), 1),),
        capacity=1,
        anonymous_row_resolver=lambda _new_line_number: 1,
    ) as projection:
        with pytest.raises(ValueError, match="absent from exact source projection"):
            projection.source_line_for(unprojected)


def test_projection_preserves_explicit_unmapped_coordinate():
    """A recorded no-line result is distinct from an unprojected row."""
    source = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "source"),
        1,
        BatchSourceSpace,
    )
    line = LineEntry(1, "+", None, 1, text_bytes=b"new", source_line=1)

    with SourceCoordinateProjection.from_pairs(
        view_identity=SnapshotIdentity("diff-view-sha256", "view"),
        source_snapshot=source,
        pairs=((DisplayLineId(1), None),),
        capacity=1,
    ) as projection:
        assert projection.source_line_for(line) is None
