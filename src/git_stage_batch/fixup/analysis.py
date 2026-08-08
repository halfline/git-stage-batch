"""Combine independent evidence for exact fixup units."""

from __future__ import annotations

from .models import (
    FixupUnit,
    FixupUnitAnalysis,
    LineageEvidence,
    PlacementEvidence,
)


def unsupported_fixup_unit_analysis(unit: FixupUnit) -> FixupUnitAnalysis:
    """Return a closed analysis for an intentionally unsupported unit."""
    return FixupUnitAnalysis(
        unit=unit,
        status="unsupported",
        target=None,
        eligible=False,
        reason_code=unit.unsupported_reason or "unsupported-change",
        lineage=LineageEvidence(
            candidates=(),
            queried_ranges=(),
            queried_line_count=0,
            resolved_line_count=0,
            in_range_line_count=0,
            conclusive=False,
        ),
        placement=PlacementEvidence(
            status="unknown",
            barrier=None,
            commuted_across=(),
            detail=unit.unsupported_reason,
        ),
    )


def combine_fixup_evidence(
    unit: FixupUnit,
    lineage: LineageEvidence,
    placement: PlacementEvidence,
) -> FixupUnitAnalysis:
    """Return the conservative decision formed from lineage and placement."""
    if placement.status == "unknown":
        return FixupUnitAnalysis(
            unit=unit,
            status="unknown",
            target=None,
            eligible=False,
            reason_code=placement.detail or "placement-analysis-failed",
            lineage=lineage,
            placement=placement,
        )

    if len(lineage.candidates) > 1:
        return FixupUnitAnalysis(
            unit=unit,
            status="ambiguous",
            target=None,
            eligible=False,
            reason_code="multiple-lineage-candidates",
            lineage=lineage,
            placement=placement,
        )

    lineage_target = lineage.unique_target
    barrier = placement.barrier
    if lineage_target is not None and barrier is not None:
        if lineage_target == barrier:
            return FixupUnitAnalysis(
                unit=unit,
                status="agreed",
                target=barrier,
                eligible=True,
                reason_code="lineage-and-placement-agree",
                lineage=lineage,
                placement=placement,
            )
        return FixupUnitAnalysis(
            unit=unit,
            status="disagreement",
            target=None,
            eligible=False,
            reason_code="lineage-and-placement-disagree",
            lineage=lineage,
            placement=placement,
        )

    if lineage_target is not None and placement.status == "commutes-through":
        return FixupUnitAnalysis(
            unit=unit,
            status="lineage-only",
            target=lineage_target,
            eligible=True,
            reason_code="unique-lineage-and-free-placement",
            lineage=lineage,
            placement=placement,
        )

    if barrier is not None:
        return FixupUnitAnalysis(
            unit=unit,
            status="placement-only",
            target=barrier,
            eligible=False,
            reason_code="placement-barrier-without-lineage",
            lineage=lineage,
            placement=placement,
        )

    return FixupUnitAnalysis(
        unit=unit,
        status="unresolved",
        target=None,
        eligible=False,
        reason_code="no-target-evidence",
        lineage=lineage,
        placement=placement,
    )
