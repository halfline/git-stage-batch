"""Translate ownership references between saved baseline files."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.mapped_storage import (
    MappedRecordVector,
    sort_mapped_records,
)
from ...core.text_lines import (
    as_acquirable_line_sequence,
    normalize_line_sequence_endings,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines
from ..ownership.model import BatchOwnership
from ..ownership.references import BaselineReference
from .baseline_reference_positions import (
    baseline_reference_insertion_position,
)


def _reference_for_insertion(
    target_lines: Sequence[bytes],
    position: int,
) -> BaselineReference:
    """Return an exact two-sided reference for one target gap."""
    after_line = position or None
    before_line = position + 1 if position < len(target_lines) else None
    return BaselineReference(
        after_line=after_line,
        after_content=(
            bytes(target_lines[position - 1]) if position > 0 else None
        ),
        has_after_line=True,
        before_line=before_line,
        before_content=(
            bytes(target_lines[position])
            if position < len(target_lines)
            else None
        ),
        has_before_line=True,
    )


def _translate_presence_references(
    ownership: BatchOwnership,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
) -> None:
    reference_count = sum(
        len(claim.baseline_references)
        for claim in ownership.presence_claims
    )
    with (
        MappedRecordVector(reference_count, "QQQ") as referenced_lines,
        MappedRecordVector(reference_count, "QQ") as dropped_references,
    ):
        previous_source_position = -1
        records_are_sorted = True
        for claim_index, claim in enumerate(ownership.presence_claims):
            for claimed_line, reference in claim.baseline_references.items():
                source_position = baseline_reference_insertion_position(
                    reference,
                    source_lines,
                )
                if source_position is None:
                    if baseline_reference_insertion_position(
                        reference,
                        target_lines,
                    ) is None:
                        dropped_references.append((
                            claim_index,
                            claimed_line,
                        ))
                    continue
                if source_position < previous_source_position:
                    records_are_sorted = False
                referenced_lines.append((
                    source_position,
                    claim_index,
                    claimed_line,
                ))
                previous_source_position = source_position

        for claim_index, claimed_line in dropped_references:
            ownership.presence_claims[
                claim_index
            ].baseline_references.pop(claimed_line, None)

        if not records_are_sorted:
            sort_mapped_records(referenced_lines)
        mapped_pairs = mapping.mapped_line_pairs()
        previous_pair: tuple[int, int] | None = None
        next_pair = next(mapped_pairs, None)
        for source_position, claim_index, claimed_line in referenced_lines:
            while next_pair is not None and next_pair[0] <= source_position:
                previous_pair = next_pair
                next_pair = next(mapped_pairs, None)

            target_position = (
                previous_pair[1] if previous_pair is not None else 0
            )
            target_before_line = (
                next_pair[1]
                if next_pair is not None
                else len(target_lines) + 1
            )
            claim = ownership.presence_claims[claim_index]
            if target_before_line != target_position + 1:
                claim.baseline_references.pop(claimed_line, None)
                continue
            claim.baseline_references[claimed_line] = _reference_for_insertion(
                target_lines,
                target_position,
            )


def translate_ownership_baseline_references(
    ownership: BatchOwnership,
    source_baseline_lines: Sequence[bytes],
    target_baseline_lines: Sequence[bytes],
) -> None:
    """Rebase newly translated ownership references onto a batch baseline."""
    normalized_source = normalize_line_sequence_endings(source_baseline_lines)
    normalized_target = normalize_line_sequence_endings(target_baseline_lines)
    source_sequence = as_acquirable_line_sequence(normalized_source)
    target_sequence = as_acquirable_line_sequence(normalized_target)

    with (
        source_sequence.acquire_lines() as source_lines,
        target_sequence.acquire_lines() as target_lines,
        match_lines(source_lines, target_lines) as mapping,
    ):
        _translate_presence_references(
            ownership,
            source_lines,
            target_lines,
            mapping,
        )
