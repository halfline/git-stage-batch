"""File-centric ownership attribution for filtering.

This module builds a file-centric ownership view and then projects that view
onto displayed diff hunks. Unit generation is derived from direct baseline ↔
working-tree comparison, while batch metadata supplements the model with owned
content that may not currently be visible in the working tree.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..utils.git_object_io import (
    GitObjectInfo,
    resolve_git_objects,
    stream_git_blobs,
)
from .line_matching.line_mapping import LineMapping as _LineMapping
from .line_matching.match import match_lines
from . import attribution_fingerprints as _attribution_fingerprints
from .attribution_units import (
    AttributionUnit as _AttributionUnit,
    AttributionUnitKind as _AttributionUnitKind,
    build_file_comparison_from_lines as _build_file_comparison_from_lines,
    enumerate_units_from_file_comparison as _enumerate_units_from_file_comparison,
    make_attribution_unit_id as _make_attribution_unit_id,
)
from .state.query import list_batch_names, read_batch_metadata_for_batches
from .state.reference_names import format_batch_state_ref_name
from ..core.line_selection import parse_line_selection
from ..utils.repository_buffers import (
    read_git_object_buffer_or_empty,
    load_working_tree_file_as_buffer,
    stream_git_blob_buffers,
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
class AttributionMetrics:
    """Phase counts for one bounded file attribution pass."""

    candidate_batches: int = 0
    claimed_batches: int = 0
    object_resolution_requests: int = 0
    object_requests: int = 0
    object_bytes: int = 0
    unique_source_contents: int = 0
    mapping_computations: int = 0
    deletion_fingerprints: int = 0
    attributed_units: int = 0


@dataclass
class BatchAttributionContext:
    """Reusable source alignment for one batch/file attribution pass."""

    batch_name: str
    file_metadata: dict
    batch_source_lines: Sequence[bytes]
    alignment: _LineMapping
    deletion_fingerprints: dict[
        str,
        _attribution_fingerprints.ContentFingerprint,
    ]
    presence_source_lines: tuple[int, ...]
    presence_source_line_set: frozenset[int]


@dataclass(frozen=True)
class _BatchSourceRequest:
    batch_name: str
    file_metadata: dict
    primary_refspec: str
    fallback_refspec: str


@dataclass(frozen=True)
class _ResolvedBatchSourceRequest:
    """One batch claim grouped by its canonical source blob identity."""

    batch_name: str
    file_metadata: dict
    source_object_id: str | None
    presence_source_lines: tuple[int, ...]


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
        file_metadata.get("presence_claims") or file_metadata.get("claimed_lines")
    )


def build_file_attribution(
    file_path: str,
    *,
    batch_metadata_by_name: dict[str, dict] | None = None,
    supplemental_batch_metadata: dict[str, dict] | None = None,
    metrics: AttributionMetrics | None = None,
) -> FileAttribution:
    """Open repository buffers and build complete ownership attribution."""
    with (
        read_git_object_buffer_or_empty(f"HEAD:{file_path}") as baseline_lines,
        load_working_tree_file_as_buffer(file_path) as working_tree_lines,
    ):
        return build_file_attribution_from_lines(
            file_path,
            baseline_lines=baseline_lines,
            working_tree_lines=working_tree_lines,
            batch_metadata_by_name=batch_metadata_by_name,
            supplemental_batch_metadata=supplemental_batch_metadata,
            metrics=metrics,
        )


def build_file_attribution_from_lines(
    file_path: str,
    *,
    baseline_lines: Sequence[bytes],
    working_tree_lines: Sequence[bytes],
    batch_metadata_by_name: dict[str, dict] | None = None,
    supplemental_batch_metadata: dict[str, dict] | None = None,
    metrics: AttributionMetrics | None = None,
) -> FileAttribution:
    """Build file attribution from caller-owned indexed line sequences."""
    all_batch_metadata = {}
    if batch_metadata_by_name is None:
        batch_metadata_by_name = read_batch_metadata_for_batches(list_batch_names())
    if metrics is not None:
        metrics.candidate_batches = len(batch_metadata_by_name)

    # Capture names before merging supplemental metadata. Only entries from the
    # primary metadata map may resolve source_path through state-backed storage.
    # Supplemental entries, including __consumed__, use batch_source_commit.
    state_backed_batch_names = frozenset(batch_metadata_by_name)
    for batch_name, metadata in batch_metadata_by_name.items():
        if file_path in metadata.get("files", {}):
            all_batch_metadata[batch_name] = metadata

    if supplemental_batch_metadata:
        all_batch_metadata.update(supplemental_batch_metadata)
    if metrics is not None:
        metrics.claimed_batches = len(all_batch_metadata)

    all_units_map: dict[str, _AttributionUnit] = {}
    with _build_file_comparison_from_lines(
        file_path,
        baseline_lines=baseline_lines,
        working_tree_lines=working_tree_lines,
    ) as comparison:
        _enumerate_units_from_file_comparison(comparison, all_units_map)

    baseline_unit_ids = tuple(all_units_map)
    owners_by_unit_id = {unit_id: set() for unit_id in baseline_unit_ids}
    _attribute_batches(
        file_path,
        all_batch_metadata,
        state_backed_batch_names=state_backed_batch_names,
        working_tree_lines=working_tree_lines,
        all_units_map=all_units_map,
        baseline_unit_ids=baseline_unit_ids,
        owners_by_unit_id=owners_by_unit_id,
        metrics=metrics,
    )

    attributed_units = [
        AttributedUnit(
            unit=unit,
            owning_batches=owners_by_unit_id[unit_id],
        )
        for unit_id, unit in all_units_map.items()
    ]
    if metrics is not None:
        metrics.attributed_units = len(attributed_units)

    return FileAttribution(file_path=file_path, units=attributed_units)


def _attribute_batches(
    file_path: str,
    all_batch_metadata: dict,
    *,
    state_backed_batch_names: frozenset[str],
    working_tree_lines: Sequence[bytes],
    all_units_map: dict[str, _AttributionUnit],
    baseline_unit_ids: Sequence[str],
    owners_by_unit_id: dict[str, set[str]],
    metrics: AttributionMetrics | None = None,
) -> None:
    """Attribute claims while retaining one source alignment at a time."""
    source_requests = _batch_source_requests(
        file_path,
        all_batch_metadata,
        state_backed_batch_names=state_backed_batch_names,
    )
    deletion_blob_ids = _deletion_blob_ids(source_requests)
    object_names = list(
        dict.fromkeys(
            [
                *_source_refspecs(source_requests),
                *deletion_blob_ids,
            ]
        )
    )
    object_infos = resolve_git_objects(object_names)
    resolved_requests = _resolve_batch_source_requests(
        source_requests,
        object_infos,
    )
    deletion_fingerprints, deletion_object_ids, deletion_bytes = (
        _read_deletion_fingerprints(deletion_blob_ids, object_infos)
    )
    source_groups: dict[str | None, list[_ResolvedBatchSourceRequest]] = {}
    for request in resolved_requests:
        source_groups.setdefault(request.source_object_id, []).append(request)
    source_object_ids = [
        object_id for object_id in source_groups if object_id is not None
    ]

    if metrics is not None:
        metrics.object_resolution_requests = len(object_names)
        metrics.object_requests = len(deletion_object_ids) + len(source_object_ids)
        metrics.object_bytes = deletion_bytes
        metrics.deletion_fingerprints = len(deletion_fingerprints)
        metrics.unique_source_contents = len(source_groups)
        metrics.mapping_computations = len(source_groups)

    if source_object_ids:
        for source_blob in stream_git_blob_buffers(source_object_ids):
            if metrics is not None:
                metrics.object_bytes += source_blob.size
            _attribute_source_group(
                file_path,
                source_groups[source_blob.object_id],
                source_lines=source_blob.buffer,
                working_tree_lines=working_tree_lines,
                deletion_fingerprints=deletion_fingerprints,
                all_units_map=all_units_map,
                baseline_unit_ids=baseline_unit_ids,
                owners_by_unit_id=owners_by_unit_id,
            )

    empty_source_group = source_groups.get(None)
    if empty_source_group:
        _attribute_source_group(
            file_path,
            empty_source_group,
            source_lines=(),
            working_tree_lines=working_tree_lines,
            deletion_fingerprints=deletion_fingerprints,
            all_units_map=all_units_map,
            baseline_unit_ids=baseline_unit_ids,
            owners_by_unit_id=owners_by_unit_id,
        )


def _resolve_batch_source_requests(
    requests: Sequence[_BatchSourceRequest],
    object_infos: dict[str, GitObjectInfo],
) -> list[_ResolvedBatchSourceRequest]:
    """Resolve primary/fallback expressions to canonical source blob IDs."""
    resolved = []
    for request in requests:
        source_info = object_infos.get(request.primary_refspec)
        if source_info is None or source_info.object_type != "blob":
            source_info = object_infos.get(request.fallback_refspec)
        source_object_id = (
            source_info.object_id
            if source_info is not None
            and source_info.object_type == "blob"
            and source_info.size > 0
            else None
        )
        resolved.append(
            _ResolvedBatchSourceRequest(
                batch_name=request.batch_name,
                file_metadata=request.file_metadata,
                source_object_id=source_object_id,
                presence_source_lines=tuple(
                    _parse_presence_source_lines(request.file_metadata)
                ),
            )
        )
    return resolved


def _read_deletion_fingerprints(
    deletion_blob_ids: Sequence[str],
    object_infos: dict[str, GitObjectInfo],
) -> tuple[
    dict[str, _attribution_fingerprints.ContentFingerprint],
    list[str],
    int,
]:
    """Stream deletion blobs and retain only canonical fingerprints."""
    object_id_by_request = {
        blob_id: info.object_id
        for blob_id in deletion_blob_ids
        if (info := object_infos.get(blob_id)) is not None
        and info.object_type == "blob"
    }
    unique_object_ids = list(dict.fromkeys(object_id_by_request.values()))
    fingerprints_by_object_id = {}
    byte_count = 0
    if unique_object_ids:
        for blob in stream_git_blobs(unique_object_ids):
            byte_count += blob.size
            fingerprints_by_object_id[blob.object_id] = (
                _attribution_fingerprints.fingerprint_chunks(blob.content_chunks)
            )
    return (
        {
            request: fingerprints_by_object_id[object_id]
            for request, object_id in object_id_by_request.items()
            if object_id in fingerprints_by_object_id
        },
        unique_object_ids,
        byte_count,
    )


def _attribute_source_group(
    file_path: str,
    requests: Sequence[_ResolvedBatchSourceRequest],
    *,
    source_lines: Sequence[bytes],
    working_tree_lines: Sequence[bytes],
    deletion_fingerprints: dict[
        str,
        _attribution_fingerprints.ContentFingerprint,
    ],
    all_units_map: dict[str, _AttributionUnit],
    baseline_unit_ids: Sequence[str],
    owners_by_unit_id: dict[str, set[str]],
) -> None:
    """Attribute every batch sharing one source/target mapping."""
    with match_lines(
        source_lines=source_lines,
        target_lines=working_tree_lines,
    ) as alignment:
        for request in requests:
            context = BatchAttributionContext(
                batch_name=request.batch_name,
                file_metadata=request.file_metadata,
                batch_source_lines=source_lines,
                alignment=alignment,
                deletion_fingerprints=deletion_fingerprints,
                presence_source_lines=request.presence_source_lines,
                presence_source_line_set=frozenset(request.presence_source_lines),
            )
            generated_unit_ids = _enumerate_units_from_batch(
                file_path,
                context,
                all_units_map,
            )
            candidate_unit_ids = dict.fromkeys(
                [
                    *baseline_unit_ids,
                    *generated_unit_ids,
                ]
            )
            for unit_id in candidate_unit_ids:
                owners_by_unit_id.setdefault(unit_id, set())
                if _batch_owns_unit(all_units_map[unit_id], context):
                    owners_by_unit_id[unit_id].add(request.batch_name)


def _batch_source_requests(
    file_path: str,
    all_batch_metadata: dict,
    *,
    state_backed_batch_names: frozenset[str],
) -> list[_BatchSourceRequest]:
    requests: list[_BatchSourceRequest] = []
    for batch_name in sorted(all_batch_metadata):
        batch_metadata = all_batch_metadata[batch_name]
        file_metadata = batch_metadata["files"][file_path]
        fallback_refspec = f"{file_metadata['batch_source_commit']}:{file_path}"
        source_path = file_metadata.get("source_path")
        primary_refspec = fallback_refspec
        if source_path and batch_name in state_backed_batch_names:
            primary_refspec = (
                f"{format_batch_state_ref_name(batch_name)}:{source_path}"
            )
        requests.append(
            _BatchSourceRequest(
                batch_name=batch_name,
                file_metadata=file_metadata,
                primary_refspec=primary_refspec,
                fallback_refspec=fallback_refspec,
            )
        )
    return requests


def _source_refspecs(source_requests: Sequence[_BatchSourceRequest]) -> list[str]:
    refspecs: list[str] = []
    for request in source_requests:
        refspecs.append(request.primary_refspec)
        if request.fallback_refspec != request.primary_refspec:
            refspecs.append(request.fallback_refspec)
    return refspecs


def _deletion_blob_ids(
    source_requests: Sequence[_BatchSourceRequest],
) -> list[str]:
    """Return unique deletion blobs required by one file attribution pass."""
    blob_ids: list[str] = []
    for request in source_requests:
        for deletion in request.file_metadata.get("deletions", []):
            blob_id = deletion.get("blob")
            if isinstance(blob_id, str) and blob_id:
                blob_ids.append(blob_id)
    return list(dict.fromkeys(blob_ids))


def _enumerate_units_from_batch(
    file_path: str,
    batch_context: BatchAttributionContext,
    units_map: dict[str, _AttributionUnit],
) -> list[str]:
    """Add units claimed by one batch and return their semantic IDs."""
    file_metadata = batch_context.file_metadata
    batch_source_lines = batch_context.batch_source_lines
    alignment = batch_context.alignment
    generated_unit_ids: list[str] = []

    if len(batch_source_lines) == 0 and _has_presence_source_lines(file_metadata):
        return generated_unit_ids

    for source_line in batch_context.presence_source_lines:
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
        generated_unit_ids.append(unit.unit_id)

    for deletion_entry in file_metadata.get("deletions", []):
        blob_hash = deletion_entry.get("blob")
        if not blob_hash:
            continue

        deletion_fingerprint = batch_context.deletion_fingerprints.get(blob_hash)
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
        generated_unit_ids.append(unit.unit_id)

    return generated_unit_ids


def _batch_owns_unit(
    unit: _AttributionUnit,
    batch_context: BatchAttributionContext,
) -> bool:
    """Return whether one batch context owns one attribution unit."""
    if unit.kind == _AttributionUnitKind.PRESENCE_ONLY:
        return _batch_owns_presence_unit(unit, batch_context)
    if unit.kind == _AttributionUnitKind.DELETION_ONLY:
        return _batch_owns_deletion_unit(unit, batch_context)
    if unit.kind == _AttributionUnitKind.REPLACEMENT:
        return _batch_owns_presence_unit(
            unit, batch_context
        ) and _batch_owns_deletion_unit(unit, batch_context)
    return False


def _batch_owns_presence_unit(
    unit: _AttributionUnit,
    batch_context: BatchAttributionContext,
) -> bool:
    """Check whether a batch owns the presence side of a unit.

    For units present in the working tree we require structural identity first,
    and then verify content. For units missing from the working tree we only
    accept claimed source lines that are themselves currently unmapped.
    """
    if unit.claimed_content is None and unit.claimed_fingerprint is None:
        return False

    claimed_source_lines = batch_context.presence_source_lines
    if not claimed_source_lines:
        return False
    claimed_source_line_set = batch_context.presence_source_line_set
    alignment = batch_context.alignment
    batch_source_lines = batch_context.batch_source_lines

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
    batch_context: BatchAttributionContext,
) -> bool:
    """Check whether a batch owns a deletion unit via explicit absence claims."""
    if unit.deletion_content is None and unit.deletion_fingerprint is None:
        return False

    file_metadata = batch_context.file_metadata
    alignment = batch_context.alignment
    for deletion_entry in file_metadata.get("deletions", []):
        blob_hash = deletion_entry.get("blob")
        if not blob_hash:
            continue

        after_source_line = deletion_entry.get("after_source_line")
        if after_source_line is None:
            if unit.deletion_anchor_in_working_tree is not None:
                continue
        else:
            mapped_anchor = alignment.get_target_line_from_source_line(
                after_source_line
            )
            if mapped_anchor != unit.deletion_anchor_in_working_tree:
                continue

        if (
            unit.deletion_fingerprint is not None
            and batch_context.deletion_fingerprints.get(blob_hash)
            == unit.deletion_fingerprint
        ):
            return True

    return False
