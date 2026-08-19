"""Resolve explicit source alternatives before replay planning."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import overload

from ...core.coordinates import LineBoundary
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.text_lines import normalize_line_endings
from ...exceptions import MergeError
from ...i18n import _
from ..line_matching.match_workspace import MatcherWorkspace
from ..ownership.absence_claims import AbsenceClaim
from ..ownership.claims import parse_ownership_line_ranges
from ..ownership.model import BatchOwnership


def _invalid_batch_error() -> MergeError:
    return MergeError(_("Batch was created from a different version of the file"))


class _ReanchoredAbsenceClaims(Sequence[AbsenceClaim]):
    """Lazy claim view whose anchors skip superseded source alternatives."""

    def __init__(
        self,
        claims: Sequence[AbsenceClaim],
        suppressed_lines: LineRanges,
        indices: range | None = None,
    ) -> None:
        self._claims = claims
        self._suppressed_lines = suppressed_lines
        self._indices = range(len(claims)) if indices is None else indices

    def __len__(self) -> int:
        return len(self._indices)

    @overload
    def __getitem__(self, index: int) -> AbsenceClaim: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[AbsenceClaim]: ...

    def __getitem__(self, index: int | slice) -> AbsenceClaim | Sequence[AbsenceClaim]:
        if isinstance(index, slice):
            return _ReanchoredAbsenceClaims(
                self._claims,
                self._suppressed_lines,
                self._indices[index],
            )
        try:
            claim = self._claims[self._indices[index]]
        except IndexError as error:
            raise IndexError(index) from error
        anchor_line = claim.anchor_line
        if anchor_line is None or anchor_line not in self._suppressed_lines:
            return claim
        reanchored = self._suppressed_lines.nearest_unselected_at_or_before(
            anchor_line
        )
        return replace(
            claim,
            anchor=LineBoundary(
                0 if reanchored is None else reanchored
            ),
        )


@dataclass(frozen=True, slots=True)
class EffectiveMergeConstraints:
    """Replay constraints after superseded explicit alternatives are removed."""

    presence_lines: LineRanges
    deletion_claims: Sequence[AbsenceClaim]
    source_alternative_lines: LineRanges


def resolve_effective_merge_constraints(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_lines: LineRanges,
    deletion_claims: Sequence[AbsenceClaim],
    *,
    spool_dir: str | Path | None = None,
) -> EffectiveMergeConstraints:
    """Return effective replay constraints for adjacent explicit old sides.

    A source-alternative claim records its old side immediately after the
    replacement unit's selected new side.  When a later selected replacement
    reuses those lines as its own new side, the earlier explicit old side wins:
    the intermediate bytes are superseded rather than required in the result.
    """
    if not any(claim.source_alternative for claim in deletion_claims):
        return EffectiveMergeConstraints(
            presence_lines,
            deletion_claims,
            LineRanges.empty(),
        )

    suppressed_builder = LineRangeBuilder()
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        processed = workspace.int_vector(len(deletion_claims), width=4, fill=0)
        for unit in ownership.replacement_units:
            unit_lines = parse_ownership_line_ranges(unit.presence_lines)
            source_alternative_index: int | None = None
            for deletion_index in unit.deletion_indices:
                if (
                    type(deletion_index) is not int
                    or deletion_index < 0
                    or deletion_index >= len(deletion_claims)
                ):
                    raise _invalid_batch_error()
                claim = deletion_claims[deletion_index]
                if not claim.source_alternative:
                    continue
                if source_alternative_index is not None or processed[deletion_index]:
                    raise _invalid_batch_error()
                source_alternative_index = deletion_index

            if source_alternative_index is None:
                continue
            ranges = unit_lines.ranges()
            if len(ranges) != 1:
                raise _invalid_batch_error()
            unit_start, unit_end = ranges[0]
            if not presence_lines.contains_range(unit_start, unit_end):
                raise _invalid_batch_error()

            claim = deletion_claims[source_alternative_index]
            alternative_count = len(claim.content_lines)
            if alternative_count == 0:
                raise _invalid_batch_error()
            alternative_start = unit_end + 1
            alternative_end = alternative_start + alternative_count - 1
            if alternative_end > len(source_lines):
                raise _invalid_batch_error()
            for offset in range(alternative_count):
                if normalize_line_endings(
                    bytes(source_lines[alternative_start + offset - 1])
                ) != normalize_line_endings(bytes(claim.content_lines[offset])):
                    raise _invalid_batch_error()

            suppressed_builder.add_range(alternative_start, alternative_end)
            processed[source_alternative_index] = 1

        for deletion_index, claim in enumerate(deletion_claims):
            if claim.source_alternative and not processed[deletion_index]:
                raise _invalid_batch_error()

    suppressed_lines = suppressed_builder.finish()
    effective_presence = presence_lines.difference(suppressed_lines)
    return EffectiveMergeConstraints(
        effective_presence,
        _ReanchoredAbsenceClaims(deletion_claims, suppressed_lines),
        suppressed_lines,
    )
