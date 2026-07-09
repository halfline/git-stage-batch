"""Merge batch ownership metadata."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from ..core.buffer import buffer_byte_chunks
from .ownership import (
    AbsenceClaim,
    BatchOwnership,
)
from .ownership_claims import presence_claims_from_source_lines
from .ownership_references import BaselineReference
from .ownership_replacement_units import ReplacementUnit, normalize_replacement_units


@dataclass(frozen=True, slots=True)
class _AbsenceSignature:
    anchor_line: int | None
    content_digest: str
    byte_count: int
    line_count: int


def _absence_signature(claim: AbsenceClaim) -> _AbsenceSignature:
    """Return a stable signature for an absence claim."""
    digest = sha256()
    byte_count = 0
    for chunk in buffer_byte_chunks(claim.content_lines):
        digest.update(chunk)
        byte_count += len(chunk)

    return _AbsenceSignature(
        anchor_line=claim.anchor_line,
        content_digest=digest.hexdigest(),
        byte_count=byte_count,
        line_count=len(claim.content_lines),
    )


def _baseline_reference_side_score(
    reference: BaselineReference | None,
    *,
    side: str,
) -> int:
    """Score how much boundary data a baseline reference side carries."""
    if reference is None:
        return 0
    if side == "after":
        has_line = reference.has_after_line
        content = reference.after_content
    else:
        has_line = reference.has_before_line
        content = reference.before_content
    return (1 if has_line else 0) + (2 if content is not None else 0)


def _merge_baseline_references(
    existing: BaselineReference | None,
    new: BaselineReference | None,
) -> BaselineReference | None:
    """Keep the strongest available baseline boundary metadata."""
    if existing is None:
        return new
    if new is None:
        return existing

    after = (
        new
        if _baseline_reference_side_score(new, side="after")
        > _baseline_reference_side_score(existing, side="after")
        else existing
    )
    before = (
        new
        if _baseline_reference_side_score(new, side="before")
        > _baseline_reference_side_score(existing, side="before")
        else existing
    )
    return BaselineReference(
        after_line=after.after_line,
        after_content=after.after_content,
        has_after_line=after.has_after_line,
        before_line=before.before_line,
        before_content=before.before_content,
        has_before_line=before.has_before_line,
    )


def _merge_deletion_claim_metadata(
    existing: AbsenceClaim,
    new: AbsenceClaim,
) -> AbsenceClaim:
    """Merge metadata for absence claims with the same anchor and content."""
    return AbsenceClaim(
        anchor_line=existing.anchor_line,
        content_lines=existing.content_lines,
        baseline_reference=_merge_baseline_references(
            existing.baseline_reference,
            new.baseline_reference,
        ),
    )


def merge_batch_ownership(existing: BatchOwnership, new: BatchOwnership) -> BatchOwnership:
    """Merge two BatchOwnership objects.

    Combines presence claims (union) and merges deletion constraints with
    deduplication.

    Absence claims are deduplicated by (anchor_line, content) signature to prevent
    duplicate deletions when batch source advances and ownership is remapped. The
    same deletion can appear in both existing (remapped) and new (from current
    diff).

    Args:
        existing: Existing batch ownership
        new: New ownership to merge in

    Returns:
        Merged BatchOwnership with combined claims and deduplicated deletions
    """
    existing_claimed = existing.presence_line_set()
    new_claimed = new.presence_line_set()
    combined_claimed = existing_claimed.union(new_claimed)
    combined_presence_references = {
        **existing.presence_baseline_references(),
        **new.presence_baseline_references(),
    }

    combined_deletions = []
    deletion_index_by_signature: dict[_AbsenceSignature, int] = {}
    existing_deletion_index_map: dict[int, int] = {}
    new_deletion_index_map: dict[int, int] = {}

    for source_name, source_index, deletion in (
        [("existing", index, deletion) for index, deletion in enumerate(existing.deletions)]
        + [("new", index, deletion) for index, deletion in enumerate(new.deletions)]
    ):
        signature = _absence_signature(deletion)

        if signature not in deletion_index_by_signature:
            deletion_index_by_signature[signature] = len(combined_deletions)
            combined_deletions.append(deletion)
        else:
            combined_index = deletion_index_by_signature[signature]
            combined_deletions[combined_index] = _merge_deletion_claim_metadata(
                combined_deletions[combined_index],
                deletion,
            )
        combined_index = deletion_index_by_signature[signature]
        if source_name == "existing":
            existing_deletion_index_map[source_index] = combined_index
        else:
            new_deletion_index_map[source_index] = combined_index

    combined_replacement_units: list[ReplacementUnit] = []
    for source_units, index_map in (
        (existing.replacement_units, existing_deletion_index_map),
        (new.replacement_units, new_deletion_index_map),
    ):
        for unit in source_units:
            remapped_indices = [
                index_map[index]
                for index in unit.deletion_indices
                if type(index) is int and index in index_map
            ]
            combined_replacement_units.append(ReplacementUnit(
                presence_lines=unit.presence_lines,
                deletion_indices=remapped_indices,
                origin=unit.origin,
            ))

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            combined_claimed,
            combined_presence_references,
        ),
        deletions=combined_deletions,
        replacement_units=normalize_replacement_units(
            combined_replacement_units,
            deletion_count=len(combined_deletions),
        ),
    )
