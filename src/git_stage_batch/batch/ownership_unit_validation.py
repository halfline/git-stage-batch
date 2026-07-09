"""Validation helpers for ownership units."""

from __future__ import annotations

from ..exceptions import MergeError
from ..i18n import _
from .ownership_unit_types import (
    OwnershipUnit as _UnitRecord,
    OwnershipUnitKind as _UnitKind,
)


def validate_ownership_units(units: list[_UnitRecord]) -> None:
    """Validate structural invariants of ownership units."""
    deletion_claim_usage = {}

    for unit in units:
        for claim in unit.deletion_claims:
            claim_id = id(claim)
            if claim_id in deletion_claim_usage:
                # Duplicate ownership may be intentional in some future metadata.
                pass
            deletion_claim_usage[claim_id] = unit

        if unit.is_atomic:
            if unit.kind == _UnitKind.REPLACEMENT:
                if not unit.claimed_source_lines or not unit.deletion_claims:
                    raise MergeError(
                        _(
                            "Invalid replacement in batch metadata: "
                            "expected both added and removed lines."
                        )
                    )
            elif unit.kind == _UnitKind.DELETION_ONLY:
                if unit.claimed_source_lines or not unit.deletion_claims:
                    raise MergeError(
                        _(
                            "Invalid deletion in batch metadata: "
                            "expected removed lines only."
                        )
                    )
