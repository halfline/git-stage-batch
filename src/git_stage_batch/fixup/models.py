"""Value objects for staged fixup planning and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..core.buffer import LineBuffer


FixupUnitKind = Literal[
    "text-addition",
    "text-deletion",
    "text-replacement",
    "text-file-addition",
    "text-file-deletion",
    "binary",
    "rename",
    "mode",
    "gitlink",
]

FixupUnitStatus = Literal[
    "agreed",
    "lineage-only",
    "placement-only",
    "disagreement",
    "ambiguous",
    "unresolved",
    "unsupported",
    "unknown",
]

PlacementStatus = Literal["barrier", "commutes-through", "unknown"]


@dataclass(frozen=True, slots=True)
class FixupRange:
    """Canonical linear commit range considered for fixup targets."""

    base_commit: str
    head_commit: str
    commits_newest_first: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StagedFixupUnit:
    """One exact staged change unit considered for target attribution."""

    unit_id: str
    path: str
    kind: FixupUnitKind
    patch_buffer: LineBuffer | None
    old_start: int | None = None
    old_len: int | None = None
    new_start: int | None = None
    new_len: int | None = None
    anchor_line_numbers: tuple[int, ...] = ()
    unsupported_reason: str | None = None

    @property
    def is_supported_text(self) -> bool:
        """Return whether this unit can enter textual attribution."""
        return self.patch_buffer is not None and self.unsupported_reason is None


@dataclass(frozen=True, slots=True)
class LineageEvidence:
    """Range-compressed exact-line history evidence for a staged unit.

    ``candidates`` retains at most two distinct in-range owner witnesses. Two
    are sufficient to distinguish unique attribution from ambiguity without
    retaining one Python object per blamed line or owner run.
    """

    candidates: tuple[str, ...]
    queried_ranges: tuple[tuple[int, int], ...]
    queried_line_count: int
    resolved_line_count: int
    in_range_line_count: int
    conclusive: bool

    @property
    def unique_target(self) -> str | None:
        """Return the only in-range candidate, if there is exactly one."""
        if not self.conclusive or len(self.candidates) != 1:
            return None
        return self.candidates[0]


@dataclass(frozen=True, slots=True)
class PlacementEvidence:
    """Result of commuting a staged unit backward through a linear range."""

    status: PlacementStatus
    barrier: str | None
    commuted_across: tuple[str, ...]
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FixupUnitAnalysis:
    """Combined lineage and placement decision for one staged unit."""

    unit: StagedFixupUnit
    status: FixupUnitStatus
    target: str | None
    eligible: bool
    reason_code: str
    lineage: LineageEvidence
    placement: PlacementEvidence


@dataclass(frozen=True, slots=True)
class FixupTargetGroup:
    """Eligible units that will become one fixup commit for a target."""

    target: str
    subject: str
    fixup_subject: str
    hash_qualified: bool
    unit_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixupCreatePlan:
    """Frozen staged-fixup plan bound to exact repository objects."""

    schema_version: int
    object_format: str
    commit_range: FixupRange
    head_tree: str
    index_tree: str
    units: tuple[FixupUnitAnalysis, ...]
    groups: tuple[FixupTargetGroup, ...]

    @property
    def eligible_units(self) -> tuple[FixupUnitAnalysis, ...]:
        """Return units the planner permits `fixup create` to commit."""
        return tuple(unit for unit in self.units if unit.eligible)

    @property
    def remaining_units(self) -> tuple[FixupUnitAnalysis, ...]:
        """Return units that will remain staged."""
        return tuple(unit for unit in self.units if not unit.eligible)


@dataclass(frozen=True, slots=True)
class CreatedFixup:
    """One fixup commit produced from a target group."""

    target: str
    commit: str
    unit_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixupCreateResult:
    """Completed staged fixup creation result."""

    created: tuple[CreatedFixup, ...]
    recovery_ref: str
