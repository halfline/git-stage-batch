"""Model properties for exact provenance hidden behind duplicate bytes."""

from __future__ import annotations

import random

from git_stage_batch.batch.line_matching.lineage import (
    BatchSourceLineage,
    LineageRun,
)
from git_stage_batch.batch.line_matching.transforms import (
    AmbiguousPlacements,
    BatchSourceExactTransform,
    StructuralAlignment,
)
from git_stage_batch.batch.line_matching.line_mapping import LineMapping
from git_stage_batch.core.coordinates import (
    BatchSourceSpace,
    FileSnapshot,
    LineBoundary,
    SnapshotBoundary,
    SnapshotIdentity,
)


def _snapshot(identity: str, count: int):
    return FileSnapshot(
        "duplicates.txt",
        SnapshotIdentity("hidden-token-model", identity),
        count,
        BatchSourceSpace,
    )


def test_duplicate_bytes_cannot_acquire_exact_origin_authority():
    """A content alignment stays ambiguous while recorded lineage stays exact."""
    source = _snapshot("source", 3)
    target = _snapshot("target", 4)
    with StructuralAlignment(
        source,
        target,
        LineMapping(
            [2, 3, 4],
            [0, 1, 2, 3],
            may_have_unmapped_equal_lines=True,
        ),
    ) as alignment:
        structural = alignment.prove_unique_placement(
            SnapshotBoundary(source, LineBoundary(1))
        )

    with BatchSourceLineage(source_runs=(LineageRun(1, 3, 2),)) as lineage:
        exact = BatchSourceExactTransform(source, target, lineage)
        translated = exact.translate_boundary(
            SnapshotBoundary(source, LineBoundary(1))
        )

    assert isinstance(structural, AmbiguousPlacements)
    assert translated == SnapshotBoundary(target, LineBoundary(2))


def test_unrelated_duplicate_insertions_do_not_change_recorded_origins():
    """Explicit token lineage remains authoritative across duplicate payloads."""
    randomizer = random.Random(20260812)
    for case_index in range(40):
        source_count = randomizer.randint(2, 40)
        duplicate_prefix = randomizer.randint(0, 20)
        selected_offset = randomizer.randint(1, source_count)
        source = _snapshot(f"source-{case_index}", source_count)
        target = _snapshot(
            f"target-{case_index}",
            source_count + duplicate_prefix,
        )
        with BatchSourceLineage(
            source_runs=(
                LineageRun(
                    1,
                    source_count,
                    duplicate_prefix + 1,
                ),
            )
        ) as lineage:
            exact = BatchSourceExactTransform(source, target, lineage)
            translated = exact.translate_boundary(
                SnapshotBoundary(source, LineBoundary(selected_offset))
            )

        assert translated == SnapshotBoundary(
            target,
            LineBoundary(selected_offset + duplicate_prefix),
        )
