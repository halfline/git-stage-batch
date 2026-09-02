"""Build a batch's resulting file from its saved claims."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.buffer import LineBuffer
from ..core.coordinates import LineBoundary
from ..editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ..core.text_lines import normalize_line_sequence_endings
from ..exceptions import MergeError as _MergeError
from .line_matching.match import match_lines as _match_lines
from .line_matching.line_mapping import copy_line_mapping_excluding
from .merge import baseline_anchor_matching as _baseline_anchor_matching
from .merge import baseline_edits as _baseline_edits
from .merge.presence_constraints import satisfy_constraints
from .merge.presence_context import ReplacementPlacement
from .merge.source_alternative_constraints import (
    resolve_effective_merge_constraints as _resolve_effective_constraints,
)
from .ownership.absence_claims import AbsenceClaim
from .ownership.model import BatchOwnership
from .ownership.replacement_units import ReplacementUnit
from .realization.entry_storage import realized_entry_content_chunks

if TYPE_CHECKING:
    from .file_state import BatchFileState
    from .line_matching.line_mapping import LineMapping
    from .ownership.resolved_replacement_alternatives import (
        ResolvedReplacementAlternative,
    )


def _replacement_alternative_placements(
    alternatives: Sequence["ResolvedReplacementAlternative"],
    mapping: "LineMapping",
) -> tuple[ReplacementPlacement, ...]:
    """Place adjacent saved/live pairs in their shared mapped gap."""
    if not alternatives:
        return ()

    ordered = sorted(alternatives, key=lambda alternative: alternative.saved.start)
    chains: list[list[ResolvedReplacementAlternative]] = []
    for alternative in ordered:
        if (
            chains
            and alternative.saved.start == chains[-1][-1].live_envelope.end
        ):
            chains[-1].append(alternative)
        else:
            chains.append([alternative])

    before_targets: list[int | None] = []
    mapped_pairs = iter(mapping.mapped_line_pairs())
    mapped_pair = next(mapped_pairs, None)
    last_target: int | None = None
    for chain in chains:
        start_offset = chain[0].saved.start.offset
        while mapped_pair is not None and mapped_pair[0] <= start_offset:
            last_target = mapped_pair[1]
            mapped_pair = next(mapped_pairs, None)
        before_targets.append(last_target)

    after_targets: list[int | None] = []
    mapped_pairs = iter(mapping.mapped_line_pairs())
    mapped_pair = next(mapped_pairs, None)
    for chain in chains:
        end_offset = chain[-1].live_envelope.end.offset
        while mapped_pair is not None and mapped_pair[0] <= end_offset:
            mapped_pair = next(mapped_pairs, None)
        after_targets.append(None if mapped_pair is None else mapped_pair[1])

    target_line_count = len(mapping.target_to_source)
    placements: list[ReplacementPlacement] = []
    for chain, before_target, after_target in zip(
        chains,
        before_targets,
        after_targets,
        strict=True,
    ):
        before_gap = 0 if before_target is None else before_target
        after_gap = target_line_count if after_target is None else after_target - 1
        if before_gap != after_gap:
            continue
        placements.extend(
            ReplacementPlacement(
                alternative.saved_lines,
                LineBoundary(before_gap),
            )
            for alternative in chain
        )
    return tuple(placements)


def _ownership_for_realization(ownership: BatchOwnership) -> BatchOwnership:
    """Omit live source alternatives from the baseline-derived batch tree."""
    if not any(claim.source_alternative for claim in ownership.deletions):
        return ownership

    deletion_index_map: dict[int, int] = {}
    deletions: list[AbsenceClaim] = []
    for deletion_index, claim in enumerate(ownership.deletions):
        if claim.source_alternative:
            continue
        deletion_index_map[deletion_index] = len(deletions)
        deletions.append(claim)

    replacement_units = []
    for unit in ownership.replacement_units:
        remapped_indices = [
            deletion_index_map[deletion_index]
            for deletion_index in unit.deletion_indices
            if deletion_index in deletion_index_map
        ]
        if not remapped_indices:
            continue
        replacement_units.append(
            ReplacementUnit(
                presence_lines=unit.presence_lines,
                deletion_indices=remapped_indices,
                origin_evidence=unit.origin_evidence,
            )
        )
    return BatchOwnership(
        presence_claims=ownership.presence_claims,
        deletions=deletions,
        replacement_units=replacement_units,
    )


def _has_unequal_replacement_parent(ownership: "BatchOwnership") -> bool:
    """Return whether source realization needs parent-aware coordinates."""
    return any(
        unit.origin is not None
        and unit.origin.old_line_count
        != unit.origin.new_end - unit.origin.new_start + 1
        for unit in ownership.replacement_units
    )


def build_realized_buffer(
    batch_file: "BatchFileState",
    *,
    preferred_line_ending_lines: Sequence[bytes] | None = None,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Build content from a source-bound ownership aggregate."""
    batch_file.validate()
    return build_realized_buffer_from_lines(
        batch_file.baseline_lines,
        batch_file.source_lines,
        batch_file.ownership,
        preferred_line_ending_lines=preferred_line_ending_lines,
        spool_dir=spool_dir,
    )


def build_realized_buffer_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    *,
    preferred_line_ending_lines: Sequence[bytes] | None = None,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Build realized content, optionally preferring a target's line endings."""
    line_ending_sources = (
        (batch_source_lines,)
        if preferred_line_ending_lines is None
        else (preferred_line_ending_lines, batch_source_lines)
    )
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _stream_realized_content_chunks_from_lines(
                normalize_line_sequence_endings(base_lines),
                normalize_line_sequence_endings(batch_source_lines),
                ownership,
                spool_dir=spool_dir,
            ),
            choose_line_ending(*line_ending_sources),
        ),
        spool_dir=spool_dir,
    )


def _stream_realized_content_chunks_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    *,
    spool_dir: str | Path | None = None,
) -> Iterator[bytes]:
    """Yield realized batch content chunks from normalized line sequences."""
    persisted = ownership.resolve()
    effective_constraints = _resolve_effective_constraints(
        batch_source_lines,
        persisted,
        project_root_content=False,
        spool_dir=spool_dir,
    )
    collapsed_source_lines = effective_constraints.source_alternative_lines
    realization_ownership = _ownership_for_realization(ownership)
    resolved = realization_ownership.resolve()
    presence_line_set = effective_constraints.presence_lines
    deletion_claims = resolved.deletion_claims
    has_unequal_replacement_parent = _has_unequal_replacement_parent(
        realization_ownership
    )

    baseline_chunks = (
        None
        if (
            has_unequal_replacement_parent
            or effective_constraints.source_alternative_presence_lines
        )
        else _baseline_edits.try_apply_baseline_coordinate_edits(
            batch_source_lines,
            base_lines,
            realization_ownership,
            presence_line_set,
            deletion_claims,
            trust_baseline_coordinates=True,
            collapsed_source_lines=collapsed_source_lines,
            spool_dir=spool_dir,
        )
    )
    if baseline_chunks is not None:
        yield from baseline_chunks
        return

    try:
        with (
            _baseline_anchor_matching.acquire_deletion_anchor_pairs_for_target(
                batch_source_lines,
                base_lines,
                deletion_claims,
                trust_baseline_coordinates=True,
                spool_dir=spool_dir,
            ) as anchor_pairs,
            _match_lines(
                batch_source_lines,
                base_lines,
                anchor_pairs=anchor_pairs,
                spool_dir=spool_dir,
            ) as ordinary_mapping,
            (
                copy_line_mapping_excluding(
                    ordinary_mapping,
                    effective_constraints.source_alternative_presence_lines,
                    spool_dir=spool_dir,
                )
                if effective_constraints.source_alternative_presence_lines
                else nullcontext(ordinary_mapping)
            ) as mapping,
        ):
            baseline_chunks = (
                None
                if (
                    effective_constraints.source_alternative_presence_lines
                    or not (
                        realization_ownership.replacement_units
                        or collapsed_source_lines
                    )
                )
                else _baseline_edits.try_apply_baseline_coordinate_edits(
                    batch_source_lines,
                    base_lines,
                    realization_ownership,
                    presence_line_set,
                    deletion_claims,
                    allow_adjacent_unmapped_presence=True,
                    allow_mapped_independent_removals=True,
                    allow_mixed_mapped_replacement_islands=True,
                    prefer_source_mapping_for_presence=True,
                    trust_baseline_coordinates=True,
                    source_to_working_mapping=mapping,
                    collapsed_source_lines=collapsed_source_lines,
                    spool_dir=spool_dir,
                )
            )
            if baseline_chunks is None and has_unequal_replacement_parent:
                # Parent-aware coordinates are preferable when an unequal
                # replacement can be tied to the source mapping.  Some
                # historical batches contain copied context that prevents
                # that mapping proof even though their recorded baseline
                # coordinates still identify a complete, exact edit.  Keep
                # that verified plan ahead of the lenient structural path:
                # the latter can interleave mapped delimiters with the
                # replacement and then suppress the delimiter as old-side
                # content.
                baseline_chunks = _baseline_edits.try_apply_baseline_coordinate_edits(
                    batch_source_lines,
                    base_lines,
                    realization_ownership,
                    presence_line_set,
                    deletion_claims,
                    trust_baseline_coordinates=True,
                    collapsed_source_lines=collapsed_source_lines,
                    spool_dir=spool_dir,
                )
            if baseline_chunks is not None:
                yield from baseline_chunks
                return
            realized_entries = satisfy_constraints(
                batch_source_lines,
                base_lines,
                presence_line_set,
                deletion_claims,
                strict=False,
                source_to_working_mapping=mapping,
                replacement_placements=_replacement_alternative_placements(
                    persisted.replacement_alternatives,
                    mapping,
                ),
                spool_dir=spool_dir,
            )
    except _MergeError:
        baseline_chunks = _baseline_edits.try_apply_baseline_coordinate_edits(
            batch_source_lines,
            base_lines,
            realization_ownership,
            presence_line_set,
            deletion_claims,
            trust_baseline_coordinates=True,
            collapsed_source_lines=collapsed_source_lines,
            spool_dir=spool_dir,
        )
        if baseline_chunks is None:
            raise
        yield from baseline_chunks
        return

    try:
        yield from realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()
