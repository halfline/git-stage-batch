"""Compact ownership metadata used only for live attribution."""

from __future__ import annotations

from ...core.line_selection import LineRanges
from .metadata_types import (
    AbsenceClaimMetadata,
    BatchOwnershipMetadata,
    PresenceClaimMetadata,
)


def compact_ownership_metadata_for_attribution(
    metadata: BatchOwnershipMetadata,
) -> BatchOwnershipMetadata:
    """Keep only normalized claims that live attribution reads.

    Baseline references and replacement-unit coupling are needed while merging
    a canonical batch.  Attribution uses only the union of presence source
    ranges and the deletion blob/anchor pairs, so copying the other fields into
    applied provenance would add line-scale storage without changing its answer.
    """
    presence_lines = LineRanges.from_specs(
        source_line
        for claim in metadata.get("presence_claims", [])
        for source_line in claim.get("source_lines", [])
    )
    if not presence_lines and metadata.get("claimed_lines"):
        presence_lines = LineRanges.from_specs(metadata["claimed_lines"])
    compact_presence: list[PresenceClaimMetadata] = []
    if presence_lines:
        compact_presence.append({
            "source_lines": presence_lines.to_range_strings(),
        })

    compact_deletions: list[AbsenceClaimMetadata] = []
    for deletion in metadata.get("deletions", []):
        compact_deletion: AbsenceClaimMetadata = {
            "blob": deletion["blob"],
        }
        if "after_source_line" in deletion:
            compact_deletion["after_source_line"] = deletion[
                "after_source_line"
            ]
        compact_deletions.append(compact_deletion)

    return {
        "presence_claims": compact_presence,
        "deletions": compact_deletions,
    }
