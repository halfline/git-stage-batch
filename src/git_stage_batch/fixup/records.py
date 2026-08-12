"""Stable machine-readable records for exact fixup evidence."""

from __future__ import annotations

from .models import FixupUnitAnalysis


def fixup_analysis_record(analysis: FixupUnitAnalysis) -> dict[str, object]:
    """Return the stable JSON evidence record for one exact fixup unit."""
    unit = analysis.unit
    return {
        "id": unit.unit_id,
        "path": unit.path,
        "kind": unit.kind,
        "location": {
            "old_start": unit.old_start,
            "old_length": unit.old_len,
            "new_start": unit.new_start,
            "new_length": unit.new_len,
        },
        "status": analysis.status,
        "eligible": analysis.eligible,
        "target": analysis.target,
        "reason": analysis.reason_code,
        "lineage": {
            "candidate_witnesses": list(analysis.lineage.candidates),
            "conclusive": analysis.lineage.conclusive,
            "queried_ranges": [
                {
                    "start": start,
                    "end": end,
                }
                for start, end in analysis.lineage.queried_ranges
            ],
            "queried_line_count": analysis.lineage.queried_line_count,
            "resolved_line_count": analysis.lineage.resolved_line_count,
            "in_range_line_count": analysis.lineage.in_range_line_count,
        },
        "placement": {
            "status": analysis.placement.status,
            "barrier": analysis.placement.barrier,
            "commuted_across": list(analysis.placement.commuted_across),
            "detail": analysis.placement.detail,
        },
    }
