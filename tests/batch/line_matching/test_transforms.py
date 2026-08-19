"""Tests separating exact lineage from structural correspondence."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.line_matching.line_mapping import LineMapping
from git_stage_batch.batch.line_matching.lineage import (
    BatchSourceLineage,
    LineageRun,
    SourceSelectionExpansion,
)
from git_stage_batch.batch.line_matching.transforms import (
    AmbiguousPlacements,
    BatchSourceExactTransform,
    compose_exact_transforms,
    StaleEvidence,
    StructuralAlignment,
    UniquePlacement,
)
from git_stage_batch.core.coordinates import (
    BatchSourceSpace,
    DiffOldSpace,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    SnapshotBoundary,
    SnapshotIdentity,
    SnapshotSpan,
    WorktreeSpace,
)


def _snapshot(identity: str, count: int = 2):
    return FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", identity),
        count,
        BatchSourceSpace,
    )


def test_structural_alignment_does_not_grant_ambiguous_provenance():
    """A plausible content match remains explicitly non-authoritative."""
    source = _snapshot("source")
    target = _snapshot("target")
    with StructuralAlignment(
        source,
        target,
        LineMapping([1, 2], [1, 2], may_have_unmapped_equal_lines=True),
    ) as alignment:
        result = alignment.prove_unique_placement(
            SnapshotBoundary(source, LineBoundary(1))
        )

    assert isinstance(result, AmbiguousPlacements)


def test_structural_alignment_rejects_stale_snapshot_evidence():
    """Equal numeric boundaries from another snapshot do not map."""
    source = _snapshot("source")
    with StructuralAlignment(
        source,
        _snapshot("target"),
        LineMapping([1, 2], [1, 2], may_have_unmapped_equal_lines=False),
    ) as alignment:
        result = alignment.prove_unique_placement(
            SnapshotBoundary(_snapshot("stale"), LineBoundary(1))
        )

    assert isinstance(result, StaleEvidence)


def test_recorded_lineage_is_an_exact_snapshot_bound_transform():
    """Recorded source advancement may translate ordinary coordinates."""
    source = _snapshot("source")
    target = _snapshot("target", count=3)
    with BatchSourceLineage(source_runs=(LineageRun(1, 2, 2),)) as lineage:
        transform = BatchSourceExactTransform.from_source_lineage(
            source,
            target,
            lineage,
        )

        translated = transform.translate_boundary(
            SnapshotBoundary(source, LineBoundary(1))
        )

    assert isinstance(translated, SnapshotBoundary)
    assert translated == SnapshotBoundary(target, LineBoundary(2))


def test_proven_unique_structural_placement_remains_result_variant():
    """Even unique structural placement must be consumed deliberately."""
    source = _snapshot("source")
    target = _snapshot("target")
    with StructuralAlignment(
        source,
        target,
        LineMapping([1, 2], [1, 2], may_have_unmapped_equal_lines=False),
    ) as alignment:
        result = alignment.prove_unique_placement(
            SnapshotBoundary(source, LineBoundary(2))
        )

    assert isinstance(result, UniquePlacement)
    assert result.target == SnapshotBoundary(target, LineBoundary(2))


def test_exact_transforms_compose_only_through_the_same_snapshot():
    """Composition preserves endpoint identity rather than numeric coincidence."""
    source = _snapshot("source")
    middle = _snapshot("middle", count=3)
    target = _snapshot("target", count=4)
    with BatchSourceLineage(source_runs=(LineageRun(1, 2, 2),)) as first_lineage:
        with BatchSourceLineage(source_runs=(LineageRun(2, 3, 3),)) as second_lineage:
            transform = compose_exact_transforms(
                BatchSourceExactTransform(source, middle, first_lineage),
                BatchSourceExactTransform(middle, target, second_lineage),
            )
            translated = transform.translate_boundary(
                SnapshotBoundary(source, LineBoundary(1))
            )

    assert translated == SnapshotBoundary(target, LineBoundary(3))


def test_exact_transform_composition_rejects_equal_numeric_stale_endpoint():
    """A different middle identity is rejected before coordinate translation."""
    source = _snapshot("source")
    middle = _snapshot("middle", count=3)
    stale_middle = _snapshot("stale-middle", count=3)
    target = _snapshot("target", count=4)
    with BatchSourceLineage(source_runs=(LineageRun(1, 2, 2),)) as first_lineage:
        with BatchSourceLineage(source_runs=(LineageRun(2, 3, 3),)) as second_lineage:
            try:
                compose_exact_transforms(
                    BatchSourceExactTransform(source, middle, first_lineage),
                    BatchSourceExactTransform(stale_middle, target, second_lineage),
                )
            except ValueError as error:
                assert "snapshot" in str(error)
            else:
                raise AssertionError("stale transform endpoint was accepted")


def test_exact_transform_composition_rejects_another_runtime_role():
    """Equal path/content endpoints still require the same coordinate role."""
    source = _snapshot("source")
    middle = _snapshot("middle")
    wrong_role_middle = FileSnapshot(
        middle.path,
        middle.identity,
        middle.line_count,
        DiffOldSpace,
    )
    target = _snapshot("target")
    with BatchSourceLineage(source_runs=(LineageRun(1, 2, 1),)) as first_lineage:
        with BatchSourceLineage(source_runs=(LineageRun(1, 2, 1),)) as second_lineage:
            try:
                compose_exact_transforms(
                    BatchSourceExactTransform(source, middle, first_lineage),
                    BatchSourceExactTransform(  # type: ignore[arg-type]
                        wrong_role_middle,
                        target,
                        second_lineage,
                    ),
                )
            except ValueError as error:
                assert "snapshot" in str(error)
            else:
                raise AssertionError("runtime role mismatch was accepted")


def test_exact_lineage_does_not_claim_a_boundary_across_inserted_lines():
    """Line provenance alone cannot choose which side owns an insertion."""
    source = _snapshot("source", count=2)
    target = _snapshot("target", count=3)
    with BatchSourceLineage(
        source_runs=(LineageRun(1, 1, 1), LineageRun(2, 2, 3)),
    ) as lineage:
        transform = BatchSourceExactTransform(source, target, lineage)

        translated = transform.translate_boundary(
            SnapshotBoundary(source, LineBoundary(1))
        )

    assert translated is None


def test_exact_lineage_rejects_span_with_unmapped_interior_line():
    """Mapped endpoints cannot authorize a deletion hidden inside a span."""
    source = _snapshot("source", count=3)
    target = _snapshot("target", count=2)
    with BatchSourceLineage(
        source_runs=(LineageRun(1, 1, 1), LineageRun(3, 3, 2)),
    ) as lineage:
        transform = BatchSourceExactTransform(source, target, lineage)

        translated = transform.translate_span(
            SnapshotSpan(
                source,
                LineSpan(LineBoundary(0), LineBoundary(3)),
            )
        )

    assert translated is None


def test_exact_lineage_rejects_span_with_unproven_target_interior():
    """An insertion gap requires expansion evidence before joining a span."""
    source = _snapshot("source", count=3)
    target = _snapshot("target", count=4)
    with BatchSourceLineage(
        source_runs=(LineageRun(1, 1, 1), LineageRun(2, 3, 3)),
    ) as lineage:
        transform = BatchSourceExactTransform(source, target, lineage)

        translated = transform.translate_span(
            SnapshotSpan(
                source,
                LineSpan(LineBoundary(0), LineBoundary(3)),
            )
        )

    assert translated is None


def test_exact_lineage_span_includes_complete_source_expansion():
    """A recorded expansion authorizes every produced line for its source."""
    source = _snapshot("source", count=3)
    target = _snapshot("target", count=4)
    with BatchSourceLineage(
        source_runs=(LineageRun(1, 2, 1), LineageRun(3, 3, 4)),
        source_expansions=(SourceSelectionExpansion(2, 2, 2, 3),),
    ) as lineage:
        transform = BatchSourceExactTransform(source, target, lineage)

        translated = transform.translate_span(
            SnapshotSpan(
                source,
                LineSpan(LineBoundary(1), LineBoundary(2)),
            )
        )

    assert translated == SnapshotSpan(
        target,
        LineSpan(LineBoundary(1), LineBoundary(3)),
    )


def test_working_lineage_rejects_span_with_unmapped_interior_line():
    """The worktree lineage variant also validates complete span coverage."""
    working = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "working"),
        3,
        WorktreeSpace,
    )
    target = _snapshot("target", count=2)
    with BatchSourceLineage(
        working_runs=(LineageRun(1, 1, 1), LineageRun(3, 3, 2)),
    ) as lineage:
        transform = BatchSourceExactTransform.from_working_lineage(
            working,
            target,
            lineage,
        )

        translated = transform.translate_span(
            SnapshotSpan(
                working,
                LineSpan(LineBoundary(0), LineBoundary(3)),
            )
        )

    assert translated is None


def test_structural_alignment_reports_a_boundary_gap_as_ambiguous():
    """Content correspondence cannot silently choose a side of inserted text."""
    source = _snapshot("source", count=2)
    target = _snapshot("target", count=3)
    with StructuralAlignment(
        source,
        target,
        LineMapping([1, 3], [1, 0, 2], may_have_unmapped_equal_lines=False),
    ) as alignment:
        result = alignment.prove_unique_placement(
            SnapshotBoundary(source, LineBoundary(1))
        )

    assert isinstance(result, AmbiguousPlacements)
    assert result.targets == (
        SnapshotBoundary(target, LineBoundary(1)),
        SnapshotBoundary(target, LineBoundary(2)),
    )


def test_empty_source_boundary_is_not_unique_in_nonempty_target():
    """The single empty-file boundary has no content placement evidence."""
    source = _snapshot("source", count=0)
    target = _snapshot("target", count=2)
    with StructuralAlignment(
        source,
        target,
        LineMapping([], [0, 0], may_have_unmapped_equal_lines=False),
    ) as alignment:
        result = alignment.prove_unique_placement(
            SnapshotBoundary(source, LineBoundary(0))
        )

    assert isinstance(result, AmbiguousPlacements)


@pytest.mark.parametrize(
    ("source_mapping", "target_mapping", "message"),
    [
        ([1], [1, 0], "source extent"),
        ([1, 2], [1], "target extent"),
        ([1, 3], [1, 2], "outside its snapshot"),
        ([1, 2], [2, 1], "not reciprocal"),
        ([2, 1], [2, 1], "preserve line order"),
    ],
)
def test_structural_alignment_rejects_mapping_outside_snapshot_geometry(
    source_mapping,
    target_mapping,
    message,
):
    """A mapping cannot be rebound to snapshots with different geometry."""
    with pytest.raises(ValueError, match=message):
        StructuralAlignment(
            _snapshot("source"),
            _snapshot("target"),
            LineMapping(source_mapping, target_mapping),
        )


def test_structural_alignment_rejects_another_repository_path():
    """Structural correspondence is path-bound as well as snapshot-bound."""
    source = _snapshot("source")
    target = FileSnapshot(
        "other.txt",
        SnapshotIdentity("test", "target"),
        2,
        BatchSourceSpace,
    )

    with pytest.raises(ValueError, match="same path"):
        StructuralAlignment(source, target, LineMapping([1, 2], [1, 2]))


def test_structural_alignment_closes_mapping_when_validation_fails():
    """Invalid mapped evidence is released during failed construction."""
    mapping = LineMapping([1], [1])

    with pytest.raises(ValueError, match="source extent"):
        StructuralAlignment(_snapshot("source"), _snapshot("target"), mapping)

    with pytest.raises(ValueError, match="closed"):
        mapping.get_target_line_from_source_line(1)


def test_working_lineage_factory_uses_only_the_worktree_projection():
    """The explicit working variant cannot be confused with source lineage."""
    working = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "working"),
        2,
        WorktreeSpace,
    )
    target = _snapshot("target", count=3)
    with BatchSourceLineage(
        source_runs=(LineageRun(1, 2, 1),),
        working_runs=(LineageRun(1, 2, 2),),
    ) as lineage:
        transform = BatchSourceExactTransform.from_working_lineage(
            working,
            target,
            lineage,
        )

        translated = transform.translate_boundary(
            SnapshotBoundary(working, LineBoundary(1))
        )

    assert translated == SnapshotBoundary(target, LineBoundary(2))


def test_exact_lineage_factories_reject_forged_runtime_roles():
    """Static type suppression cannot relabel source or target authority."""
    source = _snapshot("source")
    wrong_source = FileSnapshot(
        source.path,
        source.identity,
        source.line_count,
        DiffOldSpace,
    )
    wrong_target = FileSnapshot(
        source.path,
        SnapshotIdentity("test", "target"),
        source.line_count,
        WorktreeSpace,
    )
    with BatchSourceLineage(source_runs=(LineageRun(1, 2, 1),)) as lineage:
        with pytest.raises(ValueError, match="role"):
            BatchSourceExactTransform.from_source_lineage(  # type: ignore[arg-type]
                wrong_source,
                source,
                lineage,
            )
        with pytest.raises(ValueError, match="role"):
            BatchSourceExactTransform.from_source_lineage(  # type: ignore[arg-type]
                source,
                wrong_target,
                lineage,
            )


@pytest.mark.parametrize(
    ("source_count", "target_count", "runs", "message"),
    [
        (2, 3, (LineageRun(1, 3, 1),), "source snapshot"),
        (2, 2, (LineageRun(1, 2, 2),), "target snapshot"),
        (
            2,
            2,
            (LineageRun(1, 1, 2), LineageRun(2, 2, 1)),
            "target order",
        ),
    ],
)
def test_source_lineage_factory_rejects_runs_outside_bound_snapshots(
    source_count,
    target_count,
    runs,
    message,
):
    """Recorded runs must fit both exact endpoint snapshots and preserve order."""
    with BatchSourceLineage(source_runs=runs) as lineage:
        with pytest.raises(ValueError, match=message):
            BatchSourceExactTransform.from_source_lineage(
                _snapshot("source", source_count),
                _snapshot("target", target_count),
                lineage,
            )


@pytest.mark.parametrize(
    ("source_count", "target_count", "expansion", "message"),
    [
        (2, 4, SourceSelectionExpansion(1, 3, 1, 4), "source snapshot"),
        (2, 3, SourceSelectionExpansion(1, 2, 1, 4), "target snapshot"),
    ],
)
def test_source_lineage_factory_validates_selection_expansion_extents(
    source_count,
    target_count,
    expansion,
    message,
):
    """Selection-only lineage evidence is bound to the same endpoints."""
    with BatchSourceLineage(
        source_expansions=(expansion,),
    ) as lineage:
        with pytest.raises(ValueError, match=message):
            BatchSourceExactTransform.from_source_lineage(
                _snapshot("source", source_count),
                _snapshot("target", target_count),
                lineage,
            )


def test_working_lineage_factory_validates_worktree_extent():
    """Working lineage cannot be rebound to a shorter worktree snapshot."""
    working = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "working"),
        1,
        WorktreeSpace,
    )
    with BatchSourceLineage(working_runs=(LineageRun(1, 2, 1),)) as lineage:
        with pytest.raises(ValueError, match="working lineage.*source snapshot"):
            BatchSourceExactTransform.from_working_lineage(
                working,
                _snapshot("target", 2),
                lineage,
            )


def test_compatibility_constructor_does_not_accept_a_role_discriminator():
    """Legacy construction stays source-only rather than string-dispatched."""
    source = _snapshot("source")
    target = _snapshot("target")
    with BatchSourceLineage(source_runs=(LineageRun(1, 2, 1),)) as lineage:
        with pytest.raises(TypeError, match="source_role"):
            BatchSourceExactTransform(  # type: ignore[call-arg]
                source,
                target,
                lineage,
                source_role="working",
            )
