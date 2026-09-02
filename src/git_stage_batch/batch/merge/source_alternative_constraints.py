"""Remove outdated saved versions before planning a merge."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, overload

from ...core.coordinates import LineBoundary
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.text_lines import normalize_line_endings
from ...exceptions import MergeError
from ...i18n import _
from ..ownership.absence_claims import AbsenceClaim
from ..line_matching.line_range_view import LineRangeView
from ..line_matching.match import match_lines
from ..ownership.model import ResolvedBatchOwnership


def _invalid_batch_error() -> MergeError:
    return MergeError(_("Batch was created from a different version of the file"))


class _ReanchoredAbsenceClaims(Sequence[AbsenceClaim]):
    """Read deletions after moving their locations out of outdated versions."""

    def __init__(
        self,
        claims: Sequence[AbsenceClaim],
        suppressed_lines: LineRanges,
        content_overrides: Mapping[int, Sequence[bytes]],
        indices: range | None = None,
    ) -> None:
        self._claims = claims
        self._suppressed_lines = suppressed_lines
        self._content_overrides = content_overrides
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
                self._content_overrides,
                self._indices[index],
            )
        try:
            claim_index = self._indices[index]
            claim = self._claims[claim_index]
        except IndexError as error:
            raise IndexError(index) from error
        content_lines = self._content_overrides.get(claim_index)
        if content_lines is not None:
            claim = replace(claim, content_lines=content_lines)
        anchor_line = claim.anchor_line
        if anchor_line is None or anchor_line not in self._suppressed_lines:
            return claim
        reanchored = self._suppressed_lines.nearest_unselected_at_or_before(anchor_line)
        return replace(
            claim,
            anchor=LineBoundary(0 if reanchored is None else reanchored),
        )


class _SelectedSourceLines(Sequence[bytes]):
    """Read selected source lines without copying their contents."""

    def __init__(
        self,
        source_lines: Sequence[bytes],
        selected_lines: LineRanges,
        *,
        view_starts: tuple[int, ...] | None = None,
        source_starts: tuple[int, ...] | None = None,
        indices: range | None = None,
    ) -> None:
        self._source_lines = source_lines
        if view_starts is None or source_starts is None:
            built_view_starts: list[int] = []
            built_source_starts: list[int] = []
            line_count = 0
            for source_start, source_end in selected_lines.ranges():
                built_view_starts.append(line_count)
                built_source_starts.append(source_start - 1)
                line_count += source_end - source_start + 1
            view_starts = tuple(built_view_starts)
            source_starts = tuple(built_source_starts)
            self._indices = range(line_count)
        else:
            self._indices = range(0) if indices is None else indices
        self._view_starts = view_starts
        self._source_starts = source_starts

    def __len__(self) -> int:
        return len(self._indices)

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return _SelectedSourceLines(
                self._source_lines,
                LineRanges.empty(),
                view_starts=self._view_starts,
                source_starts=self._source_starts,
                indices=self._indices[index],
            )
        try:
            view_index = self._indices[index]
        except IndexError as error:
            raise IndexError(index) from error
        range_index = bisect_right(self._view_starts, view_index) - 1
        if range_index < 0:
            raise IndexError(index)
        source_index = self._source_starts[range_index] + (
            view_index - self._view_starts[range_index]
        )
        return bytes(self._source_lines[source_index])

    def source_line_at(self, index: int) -> int:
        """Return the one-based source line for one view index."""
        try:
            view_index = self._indices[index]
        except IndexError as error:
            raise IndexError(index) from error
        range_index = bisect_right(self._view_starts, view_index) - 1
        if range_index < 0:
            raise IndexError(index)
        return (
            self._source_starts[range_index]
            + (view_index - self._view_starts[range_index])
            + 1
        )


def _effective_root_content_overrides(
    source_lines: Sequence[bytes],
    presence_lines: LineRanges,
    resolved: ResolvedBatchOwnership,
    *,
    spool_dir: str | Path | None,
) -> tuple[dict[int, Sequence[bytes]], tuple[int, ...]]:
    """Rebuild the current text for replacement pairs that are not nested."""
    alternatives_by_deletion = {
        alternative.deletion_index: alternative
        for alternative in resolved.replacement_alternatives
    }
    children: dict[int, list[int]] = {}
    roots: list[int] = []
    for alternative in resolved.replacement_alternatives:
        parent_index = alternative.parent_deletion_index
        if parent_index is None:
            roots.append(alternative.deletion_index)
        else:
            if parent_index not in alternatives_by_deletion:
                raise _invalid_batch_error()
            children.setdefault(parent_index, []).append(alternative.deletion_index)

    presence_ranges = presence_lines.ranges()
    presence_starts = tuple(start for start, _end in presence_ranges)
    inherited_live_presence = LineRangeBuilder()
    for alternative in resolved.replacement_alternatives:
        saved_end_line = alternative.saved.end.offset
        range_index = bisect_right(presence_starts, saved_end_line) - 1
        if range_index < 0:
            continue
        presence_start, presence_end = presence_ranges[range_index]
        if not (
            presence_start <= saved_end_line and saved_end_line + 1 <= presence_end
        ):
            continue
        for payload_span in alternative.live_payload:
            inherited_start = max(
                presence_start,
                payload_span.start.offset + 1,
            )
            inherited_end = min(presence_end, payload_span.end.offset)
            if inherited_start <= inherited_end:
                inherited_live_presence.add_range(inherited_start, inherited_end)
    removable_presence = presence_lines.difference(inherited_live_presence.finish())
    referenced_presence = LineRangeBuilder()
    for claim in resolved.presence_claims:
        for source_line in claim.baseline_references:
            referenced_presence.add_line(source_line)
    referenced_presence_lines = referenced_presence.finish()

    overrides: dict[int, Sequence[bytes]] = {}
    projected_roots: list[int] = []
    for root_index in roots:
        live_lines = LineRangeBuilder()
        descendant_saved_lines = LineRangeBuilder()
        pending = [root_index]
        while pending:
            deletion_index = pending.pop()
            alternative = alternatives_by_deletion[deletion_index]
            if deletion_index != root_index:
                descendant_saved_lines.add_range(
                    alternative.saved.start.offset + 1,
                    alternative.saved.end.offset,
                )
            for payload_span in alternative.live_payload:
                live_lines.add_range(
                    payload_span.start.offset + 1,
                    payload_span.end.offset,
                )
            pending.extend(children.get(deletion_index, ()))
        lineage_live_lines = live_lines.finish()
        projected_saved_lines = LineRangeBuilder()
        root = alternatives_by_deletion[root_index]
        referenced_saved_lines = referenced_presence_lines.intersection(
            root.saved_lines
        )
        root_reference = root.absence_claim.baseline_reference
        can_project_saved_lines = (
            referenced_saved_lines
            and referenced_saved_lines != root.saved_lines
            and root_reference is not None
            and root_reference.has_after_line
            and root_reference.after_line is None
            and root_reference.has_before_line
            and root_reference.before_line is None
        )
        if can_project_saved_lines:
            saved_view = LineRangeView(
                source_lines,
                root.saved.start.offset,
                root.saved.end.offset,
            )
            live_view = _SelectedSourceLines(source_lines, lineage_live_lines)
            with match_lines(
                saved_view,
                live_view,
                spool_dir=spool_dir,
            ) as saved_to_live:
                for (
                    saved_view_line,
                    live_view_line,
                ) in saved_to_live.mapped_line_pairs():
                    saved_source_line = root.saved.start.offset + saved_view_line
                    if saved_source_line in referenced_saved_lines:
                        projected_saved_lines.add_line(
                            live_view.source_line_at(live_view_line - 1)
                        )

        projected_lines = projected_saved_lines.finish()
        if projected_lines:
            projected_roots.append(root_index)

        effective_live_lines = lineage_live_lines.difference(
            removable_presence.union(
                descendant_saved_lines.finish().union(projected_lines)
            )
        )
        overrides[root_index] = _SelectedSourceLines(
            source_lines,
            effective_live_lines,
        )
    projected_roots.sort()
    return overrides, tuple(projected_roots)


@dataclass(frozen=True, slots=True)
class EffectiveMergeConstraints:
    """The claims left after outdated saved copies are removed."""

    presence_lines: LineRanges
    deletion_claims: Sequence[AbsenceClaim]
    source_alternative_lines: LineRanges
    source_alternative_presence_lines: LineRanges
    projected_root_deletion_indices: tuple[int, ...]


def resolve_effective_merge_constraints(
    source_lines: Sequence[bytes],
    resolved: ResolvedBatchOwnership,
    *,
    project_root_content: bool = True,
    spool_dir: str | Path | None = None,
) -> EffectiveMergeConstraints:
    """Remove live copies replaced by a later saved version.

    A live copy follows its saved copy in the source. If a later edit saves
    part of that live copy again, the older copy should not appear in the
    result.
    """
    presence_lines = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims
    if not resolved.replacement_alternatives:
        return EffectiveMergeConstraints(
            presence_lines,
            deletion_claims,
            LineRanges.empty(),
            LineRanges.empty(),
            (),
        )

    alternative_presence_builder = LineRangeBuilder()
    for alternative in resolved.replacement_alternatives:
        alternative_presence_builder.add_range(
            alternative.saved.start.offset + 1,
            alternative.saved.end.offset,
        )
        if alternative.live_envelope.end.offset > len(source_lines):
            raise _invalid_batch_error()
        for source_offset, content_line in zip(
            alternative.iter_live_source_offsets(),
            alternative.absence_claim.content_lines,
            strict=True,
        ):
            if normalize_line_endings(
                bytes(source_lines[source_offset])
            ) != normalize_line_endings(bytes(content_line)):
                raise _invalid_batch_error()
    suppressed_lines = resolved.retained_replacement_lines()
    effective_presence = presence_lines.difference(suppressed_lines)
    content_overrides, projected_roots = (
        _effective_root_content_overrides(
            source_lines,
            presence_lines,
            resolved,
            spool_dir=spool_dir,
        )
        if project_root_content
        else ({}, ())
    )
    return EffectiveMergeConstraints(
        effective_presence,
        _ReanchoredAbsenceClaims(
            deletion_claims,
            suppressed_lines,
            content_overrides,
        ),
        suppressed_lines,
        alternative_presence_builder.finish(),
        projected_roots,
    )
