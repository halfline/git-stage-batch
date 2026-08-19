"""Structural batch merge using Long Common Subsequence-based alignment."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from . import baseline_anchor_matching as _baseline_anchor_matching
from . import baseline_edits as _baseline_edits
from . import presence_constraints as _presence_constraints
from .absence_constraints import (
    ABSENCE_AMBIGUITY_PREFIX as _ABSENCE_AMBIGUITY_PREFIX,
)
from .presence_placement_choices import (
    PRESENCE_AMBIGUITY_PREFIX as _PRESENCE_AMBIGUITY_PREFIX,
    presence_resolution_decision as _presence_resolution_decision,
)
from .presence_mapping import (
    match_uncontrolled_context_lines as _match_uncontrolled_context_lines,
    match_lines_preserving_unowned_context as _match_presence_lines,
    presence_lines_requiring_context_protection as _protected_presence_lines,
)
from .presence_context import PresencePlacementAmbiguityError
from .source_alternative_constraints import (
    resolve_effective_merge_constraints as _resolve_effective_constraints,
)
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
    presence_context_line_sets as _presence_context_line_sets,
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
from ...core.coordinates import (
    FileSnapshot,
    WorktreeSpace,
    content_snapshot,
    require_same_snapshot,
)
from ...core.resource_cleanup import (
    CloseableResource,
    close_resources_on_exit,
    close_resources_preserving_first,
)
from ..file_state import BatchFileState
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
    from ...core.line_selection import LineRanges
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..realization.entry_storage import RealizedEntries


_MERGE_CANDIDATE_CAP = 50
_MAPPED_OLD_SIDE_PREFLIGHT_LIMIT = 16


def merge_batch_file_state_as_buffer(
    batch_file: BatchFileState,
    target_snapshot: FileSnapshot[WorktreeSpace],
    target_lines: Sequence[bytes],
    *,
    resolution: _MergeResolution | None = None,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Merge a source-bound batch state into one exact target snapshot."""
    if batch_file.path != target_snapshot.path:
        raise ValueError("merge target path does not match batch file")
    batch_file.validate()
    target_sequence = as_acquirable_line_sequence(target_lines)
    with target_sequence.acquire_lines() as acquired:
        require_same_snapshot(
            target_snapshot,
            content_snapshot(batch_file.path, acquired, space=WorktreeSpace),
        )
    return merge_batch_from_line_sequences_as_buffer(
        batch_file.source_lines,
        batch_file.ownership,
        target_lines,
        resolution=resolution,
        spool_dir=spool_dir,
    )


class _CoordinateStrategyAmbiguity(_MergeError):
    """Recorded coordinates and structural matching produced different bytes."""


def _close_candidate(
    candidate: Iterator[bytes] | None,
    *,
    suppress_errors: bool = False,
) -> None:
    """Close a lazy merge candidate when it owns planning resources."""
    if isinstance(candidate, CloseableResource):
        close_resources_preserving_first(
            (candidate,),
            suppress_errors=suppress_errors,
        )


def _close_candidates(
    *candidates: Iterator[bytes] | None,
    suppress_errors: bool = False,
) -> None:
    """Close all owned merge candidates without stranding later streams."""

    def owned_resources() -> Iterator[CloseableResource]:
        for candidate in candidates:
            if isinstance(candidate, CloseableResource):
                yield candidate

    close_resources_preserving_first(
        owned_resources(),
        suppress_errors=suppress_errors,
    )


@contextmanager
def _close_candidates_on_exit(
    *candidates: Iterator[bytes] | None,
) -> Iterator[None]:
    """Close lazy candidates while preserving any locally raised stream error."""
    try:
        yield
    except BaseException:
        _close_candidates(*candidates, suppress_errors=True)
        raise
    else:
        _close_candidates(*candidates)


def _close_owned_mappings(
    *mappings: LineMapping | None,
    suppress_errors: bool = False,
) -> None:
    """Close every owned mapping while preserving the first close failure."""
    close_resources_preserving_first(mappings, suppress_errors=suppress_errors)


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
        while coordinate_offset == len(coordinate_chunk) and not coordinate_done:
            try:
                coordinate_chunk = bytes(next(coordinate_iterator))
                coordinate_offset = 0
            except StopIteration:
                coordinate_done = True
        while structural_offset == len(structural_chunk) and not structural_done:
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
            coordinate_offset : coordinate_offset + compare_size
        ]
        structural_view = memoryview(structural_chunk)[
            structural_offset : structural_offset + compare_size
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
    presence_line_set: "LineRanges",
    deletion_claims: Sequence["AbsenceClaim"],
    *,
    controlled_source_lines: "LineRanges",
    source_alternative_lines: "LineRanges",
    source_to_working_mapping: LineMapping | None,
    resolution: _MergeResolution | None,
    spool_dir: str | Path | None,
) -> "RealizedEntries":
    """Build the structural candidate while owning any derived mapping."""
    owned_mapping: LineMapping | None = None
    owned_ordinary_mapping: LineMapping | None = None
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
                owned_ordinary_mapping = match_lines(
                    source_lines,
                    working_lines,
                    anchor_pairs=deletion_anchor_pairs,
                    spool_dir=spool_dir,
                )
                mapping = owned_ordinary_mapping

        if mapping is None:
            raise _MergeError(
                _("Batch was created from a different version of the file")
            )
        if owned_ordinary_mapping is not None or source_to_working_mapping is None:
            mapping_result = _match_presence_lines(
                source_lines,
                working_lines,
                controlled_source_lines,
                ownership=ownership,
                presence_lines=presence_line_set,
                preferred_context_lines=source_alternative_lines,
                ordinary_mapping=mapping,
                spool_dir=spool_dir,
                matcher=match_lines,
            )
            mapping = mapping_result.mapping
            if mapping_result.owned:
                owned_mapping = mapping
            if mapping_result.ambiguous and not _has_presence_resolution(resolution):
                raise _MergeError(
                    _("Batch was created from a different version of the file")
                )

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

        (
            distinctive_presence_context_lines,
            recorded_presence_context_lines,
        ) = _presence_context_line_sets(
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
                distinctive_presence_context_lines=(distinctive_presence_context_lines),
                recorded_presence_context_lines=(recorded_presence_context_lines),
                spool_dir=spool_dir,
            )
        except PresencePlacementAmbiguityError:
            if not _has_presence_resolution(resolution):
                raise

        result = _presence_constraints.satisfy_constraints(
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
    except BaseException:
        _close_owned_mappings(
            owned_mapping,
            owned_ordinary_mapping,
            suppress_errors=True,
        )
        raise

    try:
        _close_owned_mappings(
            owned_mapping,
            owned_ordinary_mapping,
        )
    except BaseException:
        try:
            result.close()
        except BaseException:
            pass
        raise
    return result


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
        raise _MergeError(_("Selected merge resolution is no longer valid")) from None
    remaining_decisions = {
        key: value
        for key, value in resolution.decisions.items()
        if key != _COORDINATE_STRATEGY_AMBIGUITY_KEY
    }
    effective_resolution = (
        _MergeResolution(remaining_decisions) if remaining_decisions else None
    )
    return choice, effective_resolution


def _validate_resolution_shape(resolution: _MergeResolution | None) -> None:
    """Reject decisions that no current candidate family can have emitted."""
    if resolution is None:
        return
    if len(resolution.decisions) != 1:
        raise _MergeError(_("Selected merge resolution is no longer valid"))
    key, choice = next(iter(resolution.decisions.items()))
    if type(key) is not str or type(choice) is not int or choice < 1:
        raise _MergeError(_("Selected merge resolution is no longer valid"))
    if key == _COORDINATE_STRATEGY_AMBIGUITY_KEY:
        return
    if key.startswith(
        (
            _REPLACEMENT_ORIGIN_AMBIGUITY_PREFIX,
            _PRESENCE_AMBIGUITY_PREFIX,
            _ABSENCE_AMBIGUITY_PREFIX,
        )
    ):
        return
    raise _MergeError(_("Selected merge resolution is no longer valid"))


def _has_presence_resolution(resolution: _MergeResolution | None) -> bool:
    """Return whether explicit review selected a presence-placement choice."""
    if resolution is None:
        return False
    try:
        return _presence_resolution_decision(resolution.decisions) is not None
    except ValueError:
        raise _MergeError(_("Selected merge resolution is no longer valid")) from None


def _has_absence_resolution(resolution: _MergeResolution | None) -> bool:
    """Return whether explicit review selected an absence-placement choice."""
    return resolution is not None and any(
        isinstance(key, str) and key.startswith(_ABSENCE_AMBIGUITY_PREFIX)
        for key in resolution.decisions
    )


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
    trusted_target_lines: Sequence[bytes] | None = None,
    source_to_trusted_target_mapping: LineMapping | None = None,
    trusted_target_to_working_mapping: LineMapping | None = None,
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
    normalized_trusted_target_lines = (
        None
        if trusted_target_lines is None
        else as_acquirable_line_sequence(
            normalize_line_sequence_endings(trusted_target_lines)
        )
    )
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            ensure_line_chunk_boundaries(
                _merge_batch_line_chunks(
                    normalized_source_lines,
                    ownership,
                    normalized_working_lines,
                    source_to_working_mapping=source_to_working_mapping,
                    trusted_target_lines=normalized_trusted_target_lines,
                    source_to_trusted_target_mapping=(source_to_trusted_target_mapping),
                    trusted_target_to_working_mapping=(
                        trusted_target_to_working_mapping
                    ),
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
    trusted_target_lines: Sequence[bytes] | None = None,
    source_to_trusted_target_mapping: LineMapping | None = None,
    trusted_target_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> bool:
    """Return whether a normalized line merge can be applied."""
    normalized_source_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(source_lines)
    )
    normalized_working_lines = as_acquirable_line_sequence(
        normalize_line_sequence_endings(working_lines)
    )
    normalized_trusted_target_lines = (
        None
        if trusted_target_lines is None
        else as_acquirable_line_sequence(
            normalize_line_sequence_endings(trusted_target_lines)
        )
    )
    try:
        for _chunk in _merge_batch_line_chunks(
            normalized_source_lines,
            ownership,
            normalized_working_lines,
            source_to_working_mapping=source_to_working_mapping,
            trusted_target_lines=normalized_trusted_target_lines,
            source_to_trusted_target_mapping=(source_to_trusted_target_mapping),
            trusted_target_to_working_mapping=(trusted_target_to_working_mapping),
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
        acquired_source_lines = stack.enter_context(source_lines.acquire_lines())
        acquired_working_lines = stack.enter_context(working_lines.acquire_lines())
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
            source_to_trusted_target_mapping=(source_to_trusted_target_mapping),
            trusted_target_to_working_mapping=(trusted_target_to_working_mapping),
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
    _validate_resolution_shape(resolution)
    resolved = ownership.resolve()
    effective_constraints = _resolve_effective_constraints(
        source_lines,
        ownership,
        resolved.presence_line_set,
        resolved.deletion_claims,
        spool_dir=spool_dir,
    )
    presence_line_set = effective_constraints.presence_lines
    deletion_claims = effective_constraints.deletion_claims
    source_alternative_lines = effective_constraints.source_alternative_lines
    controlled_source_lines = _protected_presence_lines(
        ownership,
        resolved.presence_line_set,
        deletion_claims,
    )
    has_recorded_coordinates = _has_recorded_baseline_coordinates(
        ownership,
        presence_line_set,
        deletion_claims,
        spool_dir=spool_dir,
    )
    strategy_choice, effective_resolution = _strategy_choice_and_effective_resolution(
        resolution
    )
    has_presence_resolution = _has_presence_resolution(effective_resolution)
    has_absence_resolution = _has_absence_resolution(effective_resolution)
    has_structural_resolution = has_presence_resolution or has_absence_resolution
    has_origin_replacement_units = any(
        getattr(unit, "origin", None) is not None
        for unit in ownership.replacement_units
    )
    replacement_resolution_unit_indices = _replacement_origin_resolution_unit_indices(
        effective_resolution
    )
    if any(
        unit_index >= len(ownership.replacement_units)
        or getattr(ownership.replacement_units[unit_index], "origin", None) is None
        for unit_index in replacement_resolution_unit_indices
    ):
        raise _MergeError(_("Selected merge resolution is no longer valid"))

    has_replacement_resolution = bool(replacement_resolution_unit_indices)

    needs_origin_resolution_preflight = has_replacement_resolution or (
        strategy_choice == _CoordinateStrategyChoice.RECORDED_COORDINATES
        and has_origin_replacement_units
    )
    needs_shared_mapping = bool(presence_line_set) or (
        needs_origin_resolution_preflight
    )
    owned_ordinary_shared_mapping = None
    ordinary_shared_mapping = source_to_working_mapping
    owned_shared_mapping = None
    shared_mapping = ordinary_shared_mapping
    shared_mapping_was_corrected = False
    shared_mapping_is_ambiguous = False
    shared_mapping_has_competing_context = False
    owned_source_to_trusted_target_mapping = None
    trusted_source_mapping = source_to_trusted_target_mapping
    owned_trusted_target_to_working_mapping = None
    trusted_working_mapping = trusted_target_to_working_mapping
    try:
        if needs_shared_mapping:
            if ordinary_shared_mapping is None:
                owned_ordinary_shared_mapping = match_lines(
                    source_lines,
                    working_lines,
                    spool_dir=spool_dir,
                )
                ordinary_shared_mapping = owned_ordinary_shared_mapping
            mapping_result = _match_presence_lines(
                source_lines,
                working_lines,
                controlled_source_lines,
                ownership=ownership,
                presence_lines=presence_line_set,
                preferred_context_lines=source_alternative_lines,
                ordinary_mapping=ordinary_shared_mapping,
                spool_dir=spool_dir,
                matcher=match_lines,
            )
            shared_mapping = mapping_result.mapping
            shared_mapping_was_corrected = mapping_result.corrected
            shared_mapping_is_ambiguous = mapping_result.ambiguous
            shared_mapping_has_competing_context = mapping_result.competing_context
            if mapping_result.owned:
                owned_shared_mapping = shared_mapping
            if shared_mapping_is_ambiguous and has_presence_resolution:
                if owned_shared_mapping is not None:
                    owned_shared_mapping.close()
                owned_shared_mapping = _match_uncontrolled_context_lines(
                    source_lines,
                    working_lines,
                    controlled_source_lines,
                    spool_dir=spool_dir,
                    matcher=match_lines,
                )
                shared_mapping = owned_shared_mapping

        coordinate_mapping = (
            ordinary_shared_mapping
            if trusted_target_lines is not None
            else shared_mapping
        )
        prefer_source_presence = shared_mapping_was_corrected or any(
            claim.source_alternative for claim in deletion_claims
        )

        if trusted_target_lines is not None and ownership.replacement_units:
            if trusted_source_mapping is None:
                owned_source_to_trusted_target_mapping = match_lines(
                    source_lines,
                    trusted_target_lines,
                    spool_dir=spool_dir,
                )
                trusted_source_mapping = owned_source_to_trusted_target_mapping
        if trusted_target_lines is not None and (
            ownership.replacement_units or deletion_claims
        ):
            if trusted_working_mapping is None:
                owned_trusted_target_to_working_mapping = match_lines(
                    trusted_target_lines,
                    working_lines,
                    spool_dir=spool_dir,
                )
                trusted_working_mapping = owned_trusted_target_to_working_mapping
    except BaseException:
        try:
            _close_owned_mappings(
                owned_trusted_target_to_working_mapping,
                owned_source_to_trusted_target_mapping,
                owned_shared_mapping,
                owned_ordinary_shared_mapping,
            )
        except BaseException:
            pass
        raise

    try:
        if (
            shared_mapping_is_ambiguous
            and not has_recorded_coordinates
            and not has_presence_resolution
        ):
            if has_origin_replacement_units:
                raise _MergeError(
                    _(
                        "Cannot reliably place split replacement: original "
                        "replacement boundary is not present"
                    )
                )
            raise _MergeError(
                _("Batch was created from a different version of the file")
            )
        if needs_origin_resolution_preflight:
            assert shared_mapping is not None
            if _has_mixed_origin_replacement_claims(
                ownership,
                presence_line_set,
                source_lines,
                shared_mapping,
                spool_dir=spool_dir,
            ):
                raise _MergeError(_("Selected merge resolution is no longer valid"))
            mapped_unit_indices = (
                None
                if strategy_choice == _CoordinateStrategyChoice.RECORDED_COORDINATES
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
                raise _MergeError(_("Selected merge resolution is no longer valid"))

        if strategy_choice == _CoordinateStrategyChoice.RECORDED_COORDINATES:
            if not has_recorded_coordinates:
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
            with _close_candidates_on_exit(selected_coordinate_candidate):
                yield from selected_coordinate_candidate
            return

        skip_coordinate_replay = (
            strategy_choice is None
            and has_origin_replacement_units
            and coordinate_mapping is not None
            and _has_mapped_origin_replacement_claims(
                ownership,
                presence_line_set,
                source_lines,
                coordinate_mapping,
                unit_indices=(
                    replacement_resolution_unit_indices
                    if has_replacement_resolution
                    else None
                ),
                spool_dir=spool_dir,
            )
        )
        mapped_coordinate_fallback = None
        if (
            skip_coordinate_replay
            and trusted_target_lines is not None
            and not has_structural_resolution
        ):
            mapped_coordinate_fallback = (
                _baseline_edits.try_apply_baseline_coordinate_edits(
                    source_lines,
                    working_lines,
                    ownership,
                    presence_line_set,
                    deletion_claims,
                    resolution=effective_resolution,
                    max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
                    source_to_working_mapping=coordinate_mapping,
                    prefer_source_mapping_for_presence=prefer_source_presence,
                    trusted_target_lines=trusted_target_lines,
                    source_to_trusted_target_mapping=trusted_source_mapping,
                    trusted_target_to_working_mapping=trusted_working_mapping,
                    spool_dir=spool_dir,
                )
            )
        if (
            strategy_choice is None
            and not skip_coordinate_replay
            and not has_structural_resolution
        ):
            fallback_chunks = _baseline_edits.try_apply_baseline_coordinate_edits(
                source_lines,
                working_lines,
                ownership,
                presence_line_set,
                deletion_claims,
                resolution=effective_resolution,
                max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
                source_to_working_mapping=coordinate_mapping,
                prefer_source_mapping_for_presence=prefer_source_presence,
                trusted_target_lines=trusted_target_lines,
                source_to_trusted_target_mapping=trusted_source_mapping,
                trusted_target_to_working_mapping=trusted_working_mapping,
                spool_dir=spool_dir,
            )
            if fallback_chunks is not None:
                if not shared_mapping_has_competing_context:
                    with _close_candidates_on_exit(fallback_chunks):
                        yield from fallback_chunks
                    return
                _close_candidate(fallback_chunks)

        if (
            shared_mapping_is_ambiguous
            and not has_presence_resolution
            and not has_recorded_coordinates
        ):
            if mapped_coordinate_fallback is not None:
                with _close_candidates_on_exit(mapped_coordinate_fallback):
                    yield from mapped_coordinate_fallback
                mapped_coordinate_fallback = None
                return
            if has_origin_replacement_units:
                raise _MergeError(
                    _(
                        "Cannot reliably place split replacement: original "
                        "replacement boundary is not present"
                    )
                )
            raise _MergeError(
                _("Batch was created from a different version of the file")
            )

        coordinate_candidate = None
        if (
            resolution is None
            and not skip_coordinate_replay
            and has_recorded_coordinates
        ):
            coordinate_candidate = _baseline_edits.try_apply_baseline_coordinate_edits(
                source_lines,
                working_lines,
                ownership,
                presence_line_set,
                deletion_claims,
                resolution=effective_resolution,
                max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
                trust_baseline_coordinates=True,
                source_to_working_mapping=coordinate_mapping,
                trusted_target_lines=trusted_target_lines,
                source_to_trusted_target_mapping=trusted_source_mapping,
                trusted_target_to_working_mapping=trusted_working_mapping,
                spool_dir=spool_dir,
            )
        if (
            shared_mapping_is_ambiguous
            and coordinate_candidate is None
            and not has_presence_resolution
            and strategy_choice != _CoordinateStrategyChoice.STRUCTURAL
        ):
            raise _MergeError(
                _("Batch was created from a different version of the file")
            )
        with _close_candidates_on_exit(
            coordinate_candidate,
            mapped_coordinate_fallback,
        ):
            try:
                realized_entries = _build_structural_realized_entries(
                    source_lines,
                    ownership,
                    working_lines,
                    presence_line_set,
                    deletion_claims,
                    controlled_source_lines=controlled_source_lines,
                    source_alternative_lines=source_alternative_lines,
                    source_to_working_mapping=shared_mapping,
                    resolution=effective_resolution,
                    spool_dir=spool_dir,
                )
            except _MergeError:
                if mapped_coordinate_fallback is None:
                    raise
                yield from mapped_coordinate_fallback
                return
            with close_resources_on_exit((realized_entries,)):
                structural_chunks = _realized_entry_content_chunks(realized_entries)
                if coordinate_candidate is None:
                    yield from structural_chunks
                else:
                    yield from _yield_identical_candidate_chunks(
                        coordinate_candidate,
                        structural_chunks,
                    )
    except BaseException:
        try:
            _close_owned_mappings(
                owned_trusted_target_to_working_mapping,
                owned_source_to_trusted_target_mapping,
                owned_shared_mapping,
                owned_ordinary_shared_mapping,
            )
        except BaseException:
            pass
        raise
    else:
        _close_owned_mappings(
            owned_trusted_target_to_working_mapping,
            owned_source_to_trusted_target_mapping,
            owned_shared_mapping,
            owned_ordinary_shared_mapping,
        )


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
