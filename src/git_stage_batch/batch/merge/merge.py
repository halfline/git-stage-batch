"""Structural batch merge using Long Common Subsequence-based alignment."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from . import baseline_anchor_matching as _baseline_anchor_matching
from . import baseline_edits as _baseline_edits
from . import presence_constraints as _presence_constraints
from .baseline_replacement_choices import (
    REPLACEMENT_ORIGIN_AMBIGUITY_PREFIX as _REPLACEMENT_ORIGIN_AMBIGUITY_PREFIX,
    replacement_origin_unit_index as _replacement_origin_unit_index,
)
from .candidate_enumeration import (
    enumerate_merge_batch_candidates_for_lines as _enumerate_merge_candidates,
)
from .candidates import (
    MergeCandidateSet as _MergeCandidateSet,
    MergeResolution as _MergeResolution,
)
from .coordinate_strategy import (
    AMBIGUITY_KEY as _COORDINATE_STRATEGY_AMBIGUITY_KEY,
    CoordinateStrategyChoice as _CoordinateStrategyChoice,
    has_recorded_baseline_coordinates as _has_recorded_baseline_coordinates,
    presence_lines_requiring_distinctive_context as _distinctive_context_lines,
)
from .validation import (
    check_structural_validity as _check_merge_structural_validity,
    has_fragmented_replacement_crossing_mapped_source_lines as _has_fragmented_replacement_crossing_mapped_source_lines,
    has_mapped_origin_replacement_claims as _has_mapped_origin_replacement_claims,
    has_mixed_origin_replacement_claims as _has_mixed_origin_replacement_claims,
    has_missing_origin_replacement_claims as _has_missing_origin_replacement_claims,
    has_unsafe_mapped_origin_old_side_claims as _has_unsafe_mapped_old_side,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines
from ..realization.entry_storage import (
    realized_entry_content_chunks as _realized_entry_content_chunks,
)
from ...core.buffer import LineBuffer
from ...editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ...editor.line_export import ensure_line_chunk_boundaries
from ...editor.piece_table import LineLike
from ...exceptions import (
    AtomicUnitError as _AtomicUnitError,
    MergeError as _MergeError,
)
from ...i18n import _
from ...core.text_lines import (
    AcquirableLineSequence,
    as_acquirable_line_sequence,
    normalize_line_sequence_endings,
)

if TYPE_CHECKING:
    from ...core.line_selection import LineSelection
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..realization.entry_storage import RealizedEntries


_MERGE_CANDIDATE_CAP = 50
_MAPPED_OLD_SIDE_PREFLIGHT_LIMIT = 16


class _CoordinateStrategyAmbiguity(_MergeError):
    """Recorded coordinates and structural matching produced different bytes."""


@runtime_checkable
class _Closeable(Protocol):
    """Resource that supports explicit cleanup."""

    def close(self) -> None: ...


def _close_candidate(candidate: Iterator[bytes] | None) -> None:
    """Close a lazy merge candidate when it owns planning resources."""
    if isinstance(candidate, _Closeable):
        candidate.close()


def _replacement_origin_resolution_unit_indices(
    resolution: _MergeResolution | None,
) -> frozenset[int]:
    """Return unit indexes named by replacement-origin decisions."""
    if resolution is None:
        return frozenset()

    unit_indices: set[int] = set()
    for key in resolution.decisions:
        if not isinstance(key, str) or not key.startswith(
            _REPLACEMENT_ORIGIN_AMBIGUITY_PREFIX
        ):
            continue
        unit_index = _replacement_origin_unit_index(key)
        if unit_index is None:
            raise _MergeError(_("Selected merge resolution is no longer valid"))
        unit_indices.add(unit_index)
    return frozenset(unit_indices)


def _coordinate_ambiguity_error() -> _CoordinateStrategyAmbiguity:
    return _CoordinateStrategyAmbiguity(
        _("Batch was created from a different version of the file")
    )


def _yield_identical_candidate_chunks(
    coordinate_chunks: Iterable[LineLike],
    structural_chunks: Iterable[LineLike],
) -> Iterator[bytes]:
    """Yield structurally chunked bytes when both candidate streams match."""
    coordinate_iterator = iter(coordinate_chunks)
    structural_iterator = iter(structural_chunks)
    coordinate_chunk = b""
    structural_chunk = b""
    coordinate_offset = 0
    structural_offset = 0
    coordinate_done = False
    structural_done = False

    while True:
        while (
            coordinate_offset == len(coordinate_chunk)
            and not coordinate_done
        ):
            try:
                coordinate_chunk = bytes(next(coordinate_iterator))
                coordinate_offset = 0
            except StopIteration:
                coordinate_done = True
        while (
            structural_offset == len(structural_chunk)
            and not structural_done
        ):
            try:
                structural_chunk = bytes(next(structural_iterator))
                structural_offset = 0
            except StopIteration:
                structural_done = True

        if coordinate_done or structural_done:
            if coordinate_done and structural_done:
                return
            raise _coordinate_ambiguity_error()

        compare_size = min(
            len(coordinate_chunk) - coordinate_offset,
            len(structural_chunk) - structural_offset,
        )
        coordinate_view = memoryview(coordinate_chunk)[
            coordinate_offset:coordinate_offset + compare_size
        ]
        structural_view = memoryview(structural_chunk)[
            structural_offset:structural_offset + compare_size
        ]
        try:
            if coordinate_view != structural_view:
                raise _coordinate_ambiguity_error()
        finally:
            coordinate_view.release()
            structural_view.release()

        coordinate_offset += compare_size
        structural_offset += compare_size
        if structural_offset == len(structural_chunk):
            yield structural_chunk


def _build_structural_realized_entries(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    presence_line_set: "LineSelection",
    deletion_claims: list["AbsenceClaim"],
    *,
    source_to_working_mapping: LineMapping | None,
    resolution: _MergeResolution | None,
    spool_dir: str | Path | None,
) -> "RealizedEntries":
    """Build the structural candidate while owning any derived mapping."""
    owned_mapping: LineMapping | None = None
    contextual_placements = None
    mapping = source_to_working_mapping
    try:
        with _baseline_anchor_matching.acquire_deletion_anchor_pairs_for_target(
            source_lines,
            working_lines,
            deletion_claims,
            spool_dir=spool_dir,
        ) as deletion_anchor_pairs:
            if mapping is None or deletion_anchor_pairs:
                owned_mapping = match_lines(
                    source_lines,
                    working_lines,
                    anchor_pairs=deletion_anchor_pairs,
                    spool_dir=spool_dir,
                )
                mapping = owned_mapping

        if _has_fragmented_replacement_crossing_mapped_source_lines(
            ownership,
            presence_line_set,
            source_lines,
            mapping,
            spool_dir=spool_dir,
        ):
            raise _MergeError(
                _(
                    "Cannot reliably place split replacement: original replacement "
                    "boundary is not present"
                )
            )

        if _has_missing_origin_replacement_claims(
            ownership,
            presence_line_set,
            source_lines,
            working_lines,
            mapping,
            spool_dir=spool_dir,
        ):
            raise _MergeError(
                _(
                    "Cannot reliably place split replacement: original replacement "
                    "boundary is not present"
                )
            )

        if _has_unsafe_mapped_old_side(
            ownership,
            presence_line_set,
            source_lines,
            working_lines,
            mapping,
            spool_dir=spool_dir,
            max_classifications=_MAPPED_OLD_SIDE_PREFLIGHT_LIMIT,
        ):
            raise _MergeError(
                _(
                    "Cannot reliably place split replacement: original replacement "
                    "boundary is not present"
                )
            )

        distinctive_presence_context_lines = _distinctive_context_lines(
            ownership,
            presence_line_set,
            deletion_claims,
            target_lines=working_lines,
            spool_dir=spool_dir,
        )

        try:
            contextual_placements = _check_merge_structural_validity(
                mapping,
                presence_line_set,
                deletion_claims,
                source_lines,
                working_lines,
                distinctive_presence_context_lines=(
                    distinctive_presence_context_lines
                ),
                spool_dir=spool_dir,
            )
        except _MergeError:
            if resolution is None:
                raise

        return _presence_constraints.satisfy_constraints(
            source_lines,
            working_lines,
            presence_line_set,
            deletion_claims,
            source_to_working_mapping=mapping,
            resolution=resolution,
            distinctive_context_lines=distinctive_presence_context_lines,
            contextual_placements=contextual_placements,
            spool_dir=spool_dir,
        )
    finally:
        if owned_mapping is not None:
            owned_mapping.close()


def _strategy_choice_and_effective_resolution(
    resolution: _MergeResolution | None,
) -> tuple[_CoordinateStrategyChoice | None, _MergeResolution | None]:
    """Separate the coordinate-strategy decision from structural decisions."""
    if (
        resolution is None
        or _COORDINATE_STRATEGY_AMBIGUITY_KEY not in resolution.decisions
    ):
        return None, resolution

    raw_choice = resolution.decisions[_COORDINATE_STRATEGY_AMBIGUITY_KEY]
    if type(raw_choice) is not int:
        raise _MergeError(_("Selected merge resolution is no longer valid"))
    try:
        choice = _CoordinateStrategyChoice(raw_choice)
    except ValueError:
        raise _MergeError(
            _("Selected merge resolution is no longer valid")
        ) from None
    remaining_decisions = {
        key: value
        for key, value in resolution.decisions.items()
        if key != _COORDINATE_STRATEGY_AMBIGUITY_KEY
    }
    effective_resolution = (
        _MergeResolution(remaining_decisions) if remaining_decisions else None
    )
    return choice, effective_resolution


def _merge_result_line_ending_from_lines(
    primary_lines: Sequence[bytes],
    fallback_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence merge output."""
    return choose_line_ending(primary_lines, fallback_lines)


def merge_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Merge line sequences and return a buffer with destination line endings."""
    result_line_ending = _merge_result_line_ending_from_lines(
        working_lines,
        source_lines,
    )
    normalized_source_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(source_lines)
    )
    normalized_working_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(working_lines)
    )
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            ensure_line_chunk_boundaries(
                _merge_batch_line_chunks(
                    normalized_source_lines,
                    ownership,
                    normalized_working_lines,
                    source_to_working_mapping=source_to_working_mapping,
                    resolution=resolution,
                    spool_dir=spool_dir,
                )
            ),
            result_line_ending,
        ),
        spool_dir=spool_dir,
    )


def can_merge_batch_from_line_sequences(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> bool:
    """Return whether a normalized line merge can be applied."""
    normalized_source_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(source_lines)
    )
    normalized_working_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(working_lines)
    )
    try:
        for _chunk in _merge_batch_line_chunks(
            normalized_source_lines,
            ownership,
            normalized_working_lines,
            source_to_working_mapping=source_to_working_mapping,
            resolution=resolution,
        ):
            pass
    except _MergeError:
        return False
    return True


def _merge_batch_line_chunks(
    source_lines: AcquirableLineSequence[bytes],
    ownership: "BatchOwnership",
    working_lines: AcquirableLineSequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    trusted_target_lines: AcquirableLineSequence[bytes] | None = None,
    source_to_trusted_target_mapping: LineMapping | None = None,
    trusted_target_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
    spool_dir: str | Path | None = None,
) -> Iterator[bytes]:
    """Merge normalized byte-line sequences and yield normalized chunks."""
    with ExitStack() as stack:
        acquired_source_lines = stack.enter_context(
            source_lines.acquire_lines()
        )
        acquired_working_lines = stack.enter_context(
            working_lines.acquire_lines()
        )
        acquired_trusted_target_lines = (
            None
            if trusted_target_lines is None
            else stack.enter_context(trusted_target_lines.acquire_lines())
        )
        yield from _merge_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            source_to_working_mapping=source_to_working_mapping,
            trusted_target_lines=acquired_trusted_target_lines,
            source_to_trusted_target_mapping=(
                source_to_trusted_target_mapping
            ),
            trusted_target_to_working_mapping=(
                trusted_target_to_working_mapping
            ),
            resolution=resolution,
            spool_dir=spool_dir,
        )


def _merge_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
    source_to_trusted_target_mapping: LineMapping | None = None,
    trusted_target_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
    spool_dir: str | Path | None = None,
) -> Iterator[bytes]:
    """Merge acquired normalized line sequences and yield normalized chunks."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims
    strategy_choice, effective_resolution = (
        _strategy_choice_and_effective_resolution(resolution)
    )
    has_origin_replacement_units = any(
        getattr(unit, "origin", None) is not None
        for unit in ownership.replacement_units
    )
    has_legacy_replacement_units = any(
        getattr(unit, "origin", None) is None
        for unit in ownership.replacement_units
    )

    replacement_resolution_unit_indices = (
        _replacement_origin_resolution_unit_indices(
            effective_resolution
        )
    )
    if any(
        unit_index >= len(ownership.replacement_units)
        or getattr(ownership.replacement_units[unit_index], "origin", None) is None
        for unit_index in replacement_resolution_unit_indices
    ):
        raise _MergeError(_("Selected merge resolution is no longer valid"))

    has_replacement_resolution = bool(
        replacement_resolution_unit_indices
    )

    needs_origin_resolution_preflight = has_replacement_resolution or (
        strategy_choice == _CoordinateStrategyChoice.RECORDED_COORDINATES
        and has_origin_replacement_units
    )
    needs_shared_mapping = (
        strategy_choice is None
        and presence_line_set
        and (
            has_origin_replacement_units
            or has_legacy_replacement_units
            or not _has_recorded_baseline_coordinates(
                ownership,
                presence_line_set,
                deletion_claims,
            )
        )
    )
    owned_shared_mapping = None
    shared_mapping = source_to_working_mapping
    if shared_mapping is None and (
        needs_origin_resolution_preflight or needs_shared_mapping
    ):
        owned_shared_mapping = match_lines(
            source_lines,
            working_lines,
            spool_dir=spool_dir,
        )
        shared_mapping = owned_shared_mapping

    owned_source_to_trusted_target_mapping = None
    trusted_source_mapping = source_to_trusted_target_mapping
    owned_trusted_target_to_working_mapping = None
    trusted_working_mapping = trusted_target_to_working_mapping
    if trusted_target_lines is not None and ownership.replacement_units:
        if trusted_source_mapping is None:
            owned_source_to_trusted_target_mapping = match_lines(
                source_lines,
                trusted_target_lines,
                spool_dir=spool_dir,
            )
            trusted_source_mapping = (
                owned_source_to_trusted_target_mapping
            )
    if (
        trusted_target_lines is not None
        and (ownership.replacement_units or deletion_claims)
    ):
        if trusted_working_mapping is None:
            owned_trusted_target_to_working_mapping = match_lines(
                trusted_target_lines,
                working_lines,
                spool_dir=spool_dir,
            )
            trusted_working_mapping = (
                owned_trusted_target_to_working_mapping
            )

    try:
        if needs_origin_resolution_preflight:
            assert shared_mapping is not None
            if _has_mixed_origin_replacement_claims(
                ownership,
                presence_line_set,
                source_lines,
                shared_mapping,
                spool_dir=spool_dir,
            ):
                raise _MergeError(
                    _("Selected merge resolution is no longer valid")
                )
            mapped_unit_indices = (
                None
                if strategy_choice
                == _CoordinateStrategyChoice.RECORDED_COORDINATES
                else replacement_resolution_unit_indices
            )
            if _has_mapped_origin_replacement_claims(
                ownership,
                presence_line_set,
                source_lines,
                shared_mapping,
                unit_indices=mapped_unit_indices,
                spool_dir=spool_dir,
            ):
                raise _MergeError(
                    _("Selected merge resolution is no longer valid")
                )

        if strategy_choice == _CoordinateStrategyChoice.RECORDED_COORDINATES:
            if not _has_recorded_baseline_coordinates(
                ownership,
                presence_line_set,
                deletion_claims,
            ):
                raise _MergeError(_("Selected merge resolution is no longer valid"))
            selected_coordinate_candidate = (
                _baseline_edits.try_apply_baseline_coordinate_edits(
                    source_lines,
                    working_lines,
                    ownership,
                    presence_line_set,
                    deletion_claims,
                    resolution=effective_resolution,
                    max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
                    trust_baseline_coordinates=True,
                    source_to_working_mapping=shared_mapping,
                    trusted_target_lines=trusted_target_lines,
                    source_to_trusted_target_mapping=trusted_source_mapping,
                    trusted_target_to_working_mapping=trusted_working_mapping,
                    spool_dir=spool_dir,
                )
            )
            if selected_coordinate_candidate is None:
                raise _MergeError(_("Selected merge resolution is no longer valid"))
            try:
                yield from selected_coordinate_candidate
            finally:
                _close_candidate(selected_coordinate_candidate)
            return

        skip_coordinate_replay = (
            strategy_choice is None
            and has_origin_replacement_units
            and shared_mapping is not None
            and _has_mapped_origin_replacement_claims(
                ownership,
                presence_line_set,
                source_lines,
                shared_mapping,
                unit_indices=(
                    replacement_resolution_unit_indices
                    if has_replacement_resolution
                    else None
                ),
                spool_dir=spool_dir,
            )
        )
        mapped_coordinate_fallback = None
        if skip_coordinate_replay and trusted_target_lines is not None:
            mapped_coordinate_fallback = (
                _baseline_edits.try_apply_baseline_coordinate_edits(
                    source_lines,
                    working_lines,
                    ownership,
                    presence_line_set,
                    deletion_claims,
                    resolution=effective_resolution,
                    max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
                    source_to_working_mapping=shared_mapping,
                    trusted_target_lines=trusted_target_lines,
                    source_to_trusted_target_mapping=trusted_source_mapping,
                    trusted_target_to_working_mapping=trusted_working_mapping,
                    spool_dir=spool_dir,
                )
            )
        if strategy_choice is None and not skip_coordinate_replay:
            fallback_chunks = _baseline_edits.try_apply_baseline_coordinate_edits(
                source_lines,
                working_lines,
                ownership,
                presence_line_set,
                deletion_claims,
                resolution=effective_resolution,
                max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
                source_to_working_mapping=shared_mapping,
                trusted_target_lines=trusted_target_lines,
                source_to_trusted_target_mapping=trusted_source_mapping,
                trusted_target_to_working_mapping=trusted_working_mapping,
                spool_dir=spool_dir,
            )
            if fallback_chunks is not None:
                try:
                    yield from fallback_chunks
                finally:
                    _close_candidate(fallback_chunks)
                return

        coordinate_candidate = None
        if (
            resolution is None
            and not skip_coordinate_replay
            and _has_recorded_baseline_coordinates(
                ownership,
                presence_line_set,
                deletion_claims,
            )
        ):
            coordinate_candidate = (
                _baseline_edits.try_apply_baseline_coordinate_edits(
                    source_lines,
                    working_lines,
                    ownership,
                    presence_line_set,
                    deletion_claims,
                    resolution=effective_resolution,
                    max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
                    trust_baseline_coordinates=True,
                    source_to_working_mapping=shared_mapping,
                    trusted_target_lines=trusted_target_lines,
                    source_to_trusted_target_mapping=trusted_source_mapping,
                    trusted_target_to_working_mapping=trusted_working_mapping,
                    spool_dir=spool_dir,
                )
            )
        try:
            try:
                realized_entries = _build_structural_realized_entries(
                    source_lines,
                    ownership,
                    working_lines,
                    presence_line_set,
                    deletion_claims,
                    source_to_working_mapping=shared_mapping,
                    resolution=effective_resolution,
                    spool_dir=spool_dir,
                )
            except _MergeError:
                if mapped_coordinate_fallback is None:
                    raise
                yield from mapped_coordinate_fallback
                return
            try:
                structural_chunks = _realized_entry_content_chunks(
                    realized_entries
                )
                if coordinate_candidate is None:
                    yield from structural_chunks
                else:
                    yield from _yield_identical_candidate_chunks(
                        coordinate_candidate,
                        structural_chunks,
                    )
            finally:
                realized_entries.close()
        finally:
            _close_candidate(coordinate_candidate)
            _close_candidate(mapped_coordinate_fallback)
    finally:
        if owned_trusted_target_to_working_mapping is not None:
            owned_trusted_target_to_working_mapping.close()
        if owned_source_to_trusted_target_mapping is not None:
            owned_source_to_trusted_target_mapping.close()
        if owned_shared_mapping is not None:
            owned_shared_mapping.close()


def enumerate_merge_batch_candidates_from_line_sequences(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    *,
    max_candidates: int = _MERGE_CANDIDATE_CAP,
    spool_dir: str | Path | None = None,
) -> _MergeCandidateSet:
    """Enumerate safe merge candidates for an otherwise-refused merge.

    The normal merge path remains ambiguity-intolerant. This helper first
    verifies that the ordinary merge refuses, then enumerates supported
    ambiguity choices one at a time.
    """
    if (
        type(max_candidates) is not int
        or max_candidates < 1
        or max_candidates > _MERGE_CANDIDATE_CAP
    ):
        raise ValueError(f"max_candidates must be between 1 and {_MERGE_CANDIDATE_CAP}")

    normalized_source_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(source_lines)
    )
    normalized_working_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(working_lines)
    )
    with (
        normalized_source_lines.acquire_lines() as acquired_source_lines,
        normalized_working_lines.acquire_lines() as acquired_working_lines,
    ):
        coordinate_strategy_ambiguous = False
        try:
            for _chunk in _merge_batch_acquired_line_chunks(
                acquired_source_lines,
                ownership,
                acquired_working_lines,
                spool_dir=spool_dir,
            ):
                pass
            return _MergeCandidateSet.ordinary_merge()
        except _AtomicUnitError:
            raise
        except _MergeError as error:
            coordinate_strategy_ambiguous = isinstance(
                error,
                _CoordinateStrategyAmbiguity,
            )

        def resolution_is_valid(candidate_resolution: _MergeResolution) -> bool:
            try:
                for _chunk in _merge_batch_acquired_line_chunks(
                    acquired_source_lines,
                    ownership,
                    acquired_working_lines,
                    resolution=candidate_resolution,
                    spool_dir=spool_dir,
                ):
                    pass
            except _MergeError:
                return False
            return True

        return _enumerate_merge_candidates(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            resolution_is_valid=resolution_is_valid,
            max_candidates=max_candidates,
            coordinate_strategies_differ=coordinate_strategy_ambiguous,
            spool_dir=spool_dir,
        )
