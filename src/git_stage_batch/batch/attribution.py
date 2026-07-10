"""File-centric ownership attribution for filtering.

This module builds a file-centric ownership view and then projects that view
onto displayed diff hunks. Unit generation is derived from direct baseline ↔
working-tree comparison, while batch metadata supplements the model with owned
content that may not currently be visible in the working tree.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass

from .line_mapping import LineMapping as _LineMapping
from .match import match_lines
from . import attribution_fingerprints as _attribution_fingerprints
from .attribution_units import (
    AttributionUnit as _AttributionUnit,
    AttributionUnitKind as _AttributionUnitKind,
    build_file_comparison_from_lines as _build_file_comparison_from_lines,
    enumerate_units_from_file_comparison as _enumerate_units_from_file_comparison,
    make_attribution_unit_id as _make_attribution_unit_id,
)
from .query import list_batch_names, read_batch_metadata
from ..core.line_selection import parse_line_selection
from ..utils.repository_buffers import (
    load_git_object_as_buffer_or_empty,
    load_working_tree_file_as_buffer,
)


@dataclass
class AttributedUnit:
    """An attribution unit plus the batches that currently own it."""

    unit: _AttributionUnit
    owning_batches: set[str]


@dataclass
class FileAttribution:
    """Complete ownership attribution for a file."""

    file_path: str
    units: list[AttributedUnit]


@dataclass
class BatchAttributionContext:
    """Reusable source alignment for one batch/file attribution pass."""

    batch_name: str
    file_metadata: dict
    batch_source_lines: Sequence[bytes]
    alignment: _LineMapping


def _parse_presence_source_lines(file_metadata: dict) -> list[int]:
    presence_lines: list[int] = []
    for claim in file_metadata.get("presence_claims", []):
        source_lines = claim.get("source_lines", [])
        if source_lines:
            presence_lines.extend(
                parse_line_selection(",".join(str(line) for line in source_lines))
            )
    legacy_claimed_lines = file_metadata.get("claimed_lines", [])
    if not presence_lines and legacy_claimed_lines:
        presence_lines.extend(
            parse_line_selection(",".join(str(line) for line in legacy_claimed_lines))
        )
    return presence_lines


def _has_presence_source_lines(file_metadata: dict) -> bool:
    return bool(
        file_metadata.get("presence_claims")
        or file_metadata.get("claimed_lines")
    )


def build_file_attribution(
    file_path: str,
    *,
    supplemental_batch_metadata: dict[str, dict] | None = None,
) -> FileAttribution:
    """Build complete ownership attribution for a file."""
    all_batch_metadata = {}
    for batch_name in list_batch_names():
        metadata = read_batch_metadata(batch_name)
        if file_path in metadata.get("files", {}):
            all_batch_metadata[batch_name] = metadata

    if supplemental_batch_metadata:
        all_batch_metadata.update(supplemental_batch_metadata)

    baseline_buffer = load_git_object_as_buffer_or_empty(f"HEAD:{file_path}")
    working_tree_buffer = load_working_tree_file_as_buffer(file_path)
    with baseline_buffer as baseline_lines, working_tree_buffer as working_tree_lines:
        with ExitStack() as batch_context_stack:
            batch_contexts = _open_batch_attribution_contexts(
                file_path,
                all_batch_metadata,
                working_tree_lines=working_tree_lines,
                stack=batch_context_stack,
            )

            all_units_map: dict[str, _AttributionUnit] = {}
            with _build_file_comparison_from_lines(
                file_path,
                baseline_lines=baseline_lines,
                working_tree_lines=working_tree_lines,
            ) as comparison:
                _enumerate_units_from_file_comparison(comparison, all_units_map)

            if batch_contexts:
                _enumerate_units_from_batches(
                    file_path,
                    batch_contexts,
                    all_units_map,
                )

            attributed_units = [
                AttributedUnit(
                    unit=unit,
                    owning_batches=_find_owning_batches(unit, batch_contexts),
                )
                for unit in all_units_map.values()
            ]

    return FileAttribution(file_path=file_path, units=attributed_units)


def _open_batch_attribution_contexts(
    file_path: str,
    all_batch_metadata: dict,
    *,
    working_tree_lines: Sequence[bytes],
    stack: ExitStack,
) -> list[BatchAttributionContext]:
    """Open reusable batch source buffers and alignments for one file."""
    contexts: list[BatchAttributionContext] = []

    for batch_name, batch_metadata in all_batch_metadata.items():
        file_metadata = batch_metadata["files"][file_path]
        batch_source_commit = file_metadata["batch_source_commit"]
        batch_source_lines = stack.enter_context(
            load_git_object_as_buffer_or_empty(f"{batch_source_commit}:{file_path}")
        )
        alignment = stack.enter_context(
            match_lines(
                source_lines=batch_source_lines,
                target_lines=working_tree_lines,
            )
        )
        contexts.append(
            BatchAttributionContext(
                batch_name=batch_name,
                file_metadata=file_metadata,
                batch_source_lines=batch_source_lines,
                alignment=alignment,
            )
        )

    return contexts


def _enumerate_units_from_batches(
    file_path: str,
    batch_contexts: Sequence[BatchAttributionContext],
    units_map: dict[str, _AttributionUnit],
) -> None:
    """Add batch-owned units that may not be visible in the working tree."""
    for batch_context in batch_contexts:
        file_metadata = batch_context.file_metadata
        batch_source_lines = batch_context.batch_source_lines
        alignment = batch_context.alignment

        if len(batch_source_lines) == 0 and _has_presence_source_lines(file_metadata):
            continue

        for source_line in _parse_presence_source_lines(file_metadata):
            if source_line < 1 or source_line > len(batch_source_lines):
                continue

            claimed_content = batch_source_lines[source_line - 1]
            working_tree_line = alignment.get_target_line_from_source_line(source_line)
            unit = _AttributionUnit(
                unit_id=_make_attribution_unit_id(
                    _AttributionUnitKind.PRESENCE_ONLY,
                    file_path,
                    claimed_line=working_tree_line,
                    claimed_content=claimed_content,
                ),
                kind=_AttributionUnitKind.PRESENCE_ONLY,
                file_path=file_path,
                claimed_line_in_working_tree=working_tree_line,
                claimed_content=claimed_content,
                deletion_anchor_in_working_tree=None,
                deletion_content=None,
            )
            units_map.setdefault(unit.unit_id, unit)

        for deletion_entry in file_metadata.get("deletions", []):
            blob_hash = deletion_entry.get("blob")
            if not blob_hash:
                continue

            deletion_fingerprint = _attribution_fingerprints.fingerprint_git_blob(
                blob_hash
            )
            if deletion_fingerprint is None:
                continue

            after_source_line = deletion_entry.get("after_source_line")
            deletion_anchor = (
                None
                if after_source_line is None
                else alignment.get_target_line_from_source_line(after_source_line)
            )

            unit = _AttributionUnit(
                unit_id=_make_attribution_unit_id(
                    _AttributionUnitKind.DELETION_ONLY,
                    file_path,
                    deletion_anchor=deletion_anchor,
                    deletion_fingerprint=deletion_fingerprint,
                ),
                kind=_AttributionUnitKind.DELETION_ONLY,
                file_path=file_path,
                claimed_line_in_working_tree=None,
                claimed_content=None,
                deletion_anchor_in_working_tree=deletion_anchor,
                deletion_content=None,
                deletion_fingerprint=deletion_fingerprint,
            )
            units_map.setdefault(unit.unit_id, unit)


def _find_owning_batches(
    unit: _AttributionUnit,
    batch_contexts: Sequence[BatchAttributionContext],
) -> set[str]:
    """Determine which batches own a given unit."""
    owning_batches: set[str] = set()

    for batch_context in batch_contexts:
        file_metadata = batch_context.file_metadata
        alignment = batch_context.alignment
        batch_source_lines = batch_context.batch_source_lines

        if unit.kind == _AttributionUnitKind.PRESENCE_ONLY:
            if _batch_owns_presence_unit(
                unit,
                file_metadata,
                alignment,
                batch_source_lines,
            ):
                owning_batches.add(batch_context.batch_name)
        elif unit.kind == _AttributionUnitKind.DELETION_ONLY:
            if _batch_owns_deletion_unit(unit, file_metadata, alignment):
                owning_batches.add(batch_context.batch_name)
        elif unit.kind == _AttributionUnitKind.REPLACEMENT:
            if (
                _batch_owns_presence_unit(
                    unit,
                    file_metadata,
                    alignment,
                    batch_source_lines,
                )
                and _batch_owns_deletion_unit(unit, file_metadata, alignment)
            ):
                owning_batches.add(batch_context.batch_name)

    return owning_batches


def _batch_owns_presence_unit(
    unit: _AttributionUnit,
    file_metadata: dict,
    alignment,
    batch_source_lines: Sequence[bytes],
) -> bool:
    """Check whether a batch owns the presence side of a unit.

    For units present in the working tree we require structural identity first,
    and then verify content. For units missing from the working tree we only
    accept claimed source lines that are themselves currently unmapped.
    """
    if unit.claimed_content is None and unit.claimed_fingerprint is None:
        return False

    claimed_source_lines = _parse_presence_source_lines(file_metadata)
    if not claimed_source_lines:
        return False
    claimed_source_line_set = set(claimed_source_lines)

    if unit.claimed_line_in_working_tree is not None:
        mapped_source_line = alignment.get_source_line_from_target_line(
            unit.claimed_line_in_working_tree
        )
        if mapped_source_line is None:
            return False
        if mapped_source_line not in claimed_source_line_set:
            return False
        if mapped_source_line < 1 or mapped_source_line > len(batch_source_lines):
            return False

        if unit.claimed_content is not None:
            return batch_source_lines[mapped_source_line - 1] == unit.claimed_content

        if unit.claimed_fingerprint is None or unit.claimed_line_count is None:
            return False

        source_line_range = range(
            mapped_source_line,
            mapped_source_line + unit.claimed_line_count,
        )
        if source_line_range.stop - 1 > len(batch_source_lines):
            return False
        if any(
            source_line not in claimed_source_line_set
            for source_line in source_line_range
        ):
            return False
        return (
            _attribution_fingerprints.fingerprint_numbered_lines(
                batch_source_lines,
                source_line_range,
            )
            == unit.claimed_fingerprint
        )

    for source_line in claimed_source_lines:
        if source_line < 1 or source_line > len(batch_source_lines):
            continue

        if unit.claimed_content is not None:
            if batch_source_lines[source_line - 1] != unit.claimed_content:
                continue
            if alignment.get_target_line_from_source_line(source_line) is None:
                return True
            continue

        if unit.claimed_fingerprint is None or unit.claimed_line_count is None:
            continue

        source_line_range = range(source_line, source_line + unit.claimed_line_count)
        if source_line_range.stop - 1 > len(batch_source_lines):
            continue
        if any(line not in claimed_source_line_set for line in source_line_range):
            continue
        if any(
            alignment.get_target_line_from_source_line(line) is not None
            for line in source_line_range
        ):
            continue
        if (
            _attribution_fingerprints.fingerprint_numbered_lines(
                batch_source_lines,
                source_line_range,
            )
            == unit.claimed_fingerprint
        ):
            return True

    return False


def _batch_owns_deletion_unit(
    unit: _AttributionUnit,
    file_metadata: dict,
    alignment,
) -> bool:
    """Check whether a batch owns a deletion unit via explicit absence claims."""
    if unit.deletion_content is None and unit.deletion_fingerprint is None:
        return False

    for deletion_entry in file_metadata.get("deletions", []):
        blob_hash = deletion_entry.get("blob")
        if not blob_hash:
            continue

        after_source_line = deletion_entry.get("after_source_line")
        if after_source_line is None:
            if unit.deletion_anchor_in_working_tree is not None:
                continue
        else:
            mapped_anchor = alignment.get_target_line_from_source_line(after_source_line)
            if mapped_anchor != unit.deletion_anchor_in_working_tree:
                continue

        if (
            unit.deletion_content is not None
            and _attribution_fingerprints.blob_matches_content(
                blob_hash,
                unit.deletion_content,
            )
        ):
            return True

        if (
            unit.deletion_fingerprint is not None
            and _attribution_fingerprints.fingerprint_git_blob(blob_hash)
            == unit.deletion_fingerprint
        ):
            return True

    return False
