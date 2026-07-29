"""Resolution values for coordinate-versus-structural merge ambiguity."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import TYPE_CHECKING

from ...core.line_selection import LineSelection

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership


AMBIGUITY_KEY = "baseline-coordinate-vs-structural"


class CoordinateStrategyChoice(Enum):
    """A reviewed choice between two valid merge strategies."""

    STRUCTURAL = 1
    RECORDED_COORDINATES = 2


def has_recorded_baseline_coordinates(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
) -> bool:
    """Return whether selected edit metadata includes a recorded coordinate."""
    for presence_claim in ownership.presence_claims:
        for claimed_line, presence_reference in (
            presence_claim.baseline_references.items()
        ):
            if (
                claimed_line in presence_line_set
                and presence_reference.has_after_line
            ):
                return True
    for deletion_claim in deletion_claims:
        deletion_reference = deletion_claim.baseline_reference
        if (
            deletion_reference is not None
            and deletion_reference.has_after_line
        ):
            return True
    for unit in ownership.replacement_units:
        origin = unit.origin
        if origin is None:
            continue
        origin_reference = origin.baseline_reference
        if origin_reference is not None and origin_reference.has_after_line:
            return True
    return False
