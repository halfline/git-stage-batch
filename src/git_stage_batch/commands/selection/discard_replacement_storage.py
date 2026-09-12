"""Save prepared replacements and merge them with existing batch ownership."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import cast

from ...batch.complete_source_replacement import (
    materialize_untracked_source_replacement,
    promote_untracked_presence_to_complete_source_replacement,
    refresh_complete_source_replacement,
)
from ...batch.state.lifecycle import create_batch
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.ownership.hunk_translation import (
    translate_hunk_selection_to_batch_ownership,
)
from ...batch.ownership.merging import merge_batch_ownership
from ...batch.ownership.remapping import remap_batch_ownership_with_lineage
from ...batch.ownership.replacement_line_runs import (
    stream_replacement_line_runs_from_lines,
)
from ...batch.line_matching.transforms import (
    BatchSourceExactTransform,
    SameContentSpanProjection,
)
from ...batch.ownership.replacement_origins import (
    ReplacementOriginSourceProjection,
    SameStreamReplacementOrigin,
)
from ...batch.ownership.claims import presence_claims_from_source_lines
from ...batch.merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
)
from ...batch.state.query import read_batch_metadata
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...batch.source.advancement import (
    advance_source_lines_preserving_existing_presence,
)
from ...batch.source.line_coordinates import (
    ExactLineageSourceCoordinates,
    IdentitySourceCoordinates,
    SourceCoordinateTransform,
    translate_display_source_coordinates,
)
from ...batch.source.projection import SourceCoordinateProjection
from ...batch.file_state import BatchMetadataRevision, SourceBoundOwnership
from ...batch.text_file_storage import add_source_bound_file_to_batch
from ...batch.state.batch_names import batch_exists
from ...core.buffer import LineBuffer
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.coordinates import (
    BatchSourceSpace,
    DisplayLineId,
    FileSnapshot,
    RewrittenWorktreeSpace,
    content_snapshot,
)
from ...batch.ownership.model import BatchOwnership
from ...batch.source.cache import load_session_batch_sources, save_session_batch_sources
from ...batch.source.snapshots import create_batch_source_commit
from ...data.file_modes import detect_file_mode
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    read_git_object_buffer_or_empty,
    load_working_tree_file_as_buffer,
)
from ...data.session import snapshot_file_if_untracked
from ...exceptions import exit_with_error
from ...git_paths import display_path
from ...i18n import _
from .discard_replacement_models import (
    DiscardLineReplacementSelection,
)
from .discard_replacement_parents import (
    _add_expanded_replacement_parents,
    _expanded_selected_replacement_parents,
    _selection_may_need_parent_expansion,
)
from .discard_replacement_boundaries import (
    _add_explicit_source_alternative_replacement,
    _exact_alternative_source_range,
    _exact_owned_prefix_source_range,
    _explicit_alternative_range,
    _explicit_owned_prefix_range,
    _refine_and_preserve_explicit_presence_span_boundary,
)


def add_discard_line_replacement_to_batch(
    batch_name: str,
    selection: DiscardLineReplacementSelection,
) -> None:
    """Persist a rewritten discard replacement selection to a batch."""
    if selection.destination.batch_name != batch_name:
        raise ValueError("replacement selection belongs to a different batch")
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    metadata = read_batch_metadata(batch_name)
    metadata_revision = BatchMetadataRevision.from_metadata(metadata)
    file_metadata = metadata.get("files", {}).get(selection.file_path)
    if (file_metadata is not None) != selection.destination.file_exists:
        raise ValueError("replacement destination file changed during preparation")

    with ExitStack() as ownership_stack:
        original_working_lines = (
            ownership_stack.enter_context(
                load_working_tree_file_as_buffer(selection.file_path)
            )
            if _selection_may_need_parent_expansion(selection)
            else None
        )
        batch_source_commit: str
        bound_ownership: SourceBoundOwnership
        try:
            alternatives = selection.replacement_alternatives
            if (
                file_metadata is None
                and alternatives is not None
                and alternatives.uses_untracked_source
            ):
                materialized = ownership_stack.enter_context(
                    materialize_untracked_source_replacement(
                        selection.rewritten_working_lines,
                        alternatives,
                    )
                )
                batch_source_commit = create_batch_source_commit(
                    selection.file_path,
                    file_buffer_override=materialized.source_buffer,
                )
                _record_session_batch_source(
                    selection.file_path,
                    batch_source_commit,
                )
                bound_ownership = materialized.bound_ownership
            elif file_metadata is None:
                batch_source_commit = create_batch_source_commit(
                    selection.file_path,
                    file_buffer_override=selection.rewritten_working_lines,
                )
                _record_session_batch_source(
                    selection.file_path,
                    batch_source_commit,
                )
                reference_source_lines = ownership_stack.enter_context(
                    read_git_object_buffer_or_empty(f"HEAD:{selection.file_path}")
                )
                batch_baseline_commit = metadata.get("baseline")
                if not isinstance(batch_baseline_commit, str) or not (
                    batch_baseline_commit
                ):
                    raise ValueError(
                        "replacement update requires a batch baseline commit"
                    )
                reference_target_lines = ownership_stack.enter_context(
                    read_git_object_buffer_or_empty(
                        f"{batch_baseline_commit}:{selection.file_path}"
                    )
                )
                with _acquire_rewritten_source_projection(
                    selection,
                    source_lines=selection.rewritten_working_lines,
                    transform=IdentitySourceCoordinates(),
                ) as source_projection:
                    origin_source_projection = SameContentSpanProjection(
                        selection.transformed_projection.rewritten_snapshot,
                        source_projection.source_snapshot,
                    )
                    ownership = _translate_rewritten_selection_ownership(
                        selection,
                        baseline_lines=reference_source_lines,
                        original_working_lines=original_working_lines,
                        rewritten_lines=selection.rewritten_working_lines,
                        exact_presence_range=(_explicit_owned_prefix_range(selection)),
                        source_projection=source_projection,
                        replacement_origin_source_projection=(origin_source_projection),
                    )
                    ownership = _expand_source_scoped_alternative_ownership(
                        ownership,
                        selection=selection,
                        source_line_count=len(selection.rewritten_working_lines),
                        alternative_range=_explicit_alternative_range(selection),
                        materialize_source_scope=True,
                    )
                    ownership = _refine_and_preserve_explicit_presence_span_boundary(
                        ownership,
                        selection=selection,
                        baseline_lines=reference_source_lines,
                        source_content_lines=selection.rewritten_working_lines,
                        replacement_origin_source_projection=(origin_source_projection),
                    )
                translate_ownership_baseline_references(
                    ownership,
                    reference_source_lines,
                    reference_target_lines,
                    replacement_origin_source_lines=reference_source_lines,
                )
                explicit_alternative_range = _explicit_alternative_range(selection)
                if explicit_alternative_range is not None:
                    explicit_presence_range = _explicit_owned_prefix_range(selection)
                    if explicit_presence_range is None:
                        raise ValueError(
                            "explicit replacement prefix has no source range"
                        )
                    ownership = _add_explicit_source_alternative_replacement(
                        ownership,
                        selection=selection,
                        presence_range=explicit_presence_range,
                        alternative_range=explicit_alternative_range,
                    )
                bound_ownership = SourceBoundOwnership(
                    content_snapshot(
                        selection.file_path,
                        selection.rewritten_working_lines,
                        space=BatchSourceSpace,
                    ),
                    ownership,
                )
            else:
                bound_ownership, batch_source_commit = _merge_replacement_with_batch(
                    selection,
                    file_metadata=file_metadata,
                    batch_baseline_commit=metadata.get("baseline"),
                    original_working_lines=original_working_lines,
                    ownership_stack=ownership_stack,
                )
        except ValueError as e:
            exit_with_error(
                _(
                    "Cannot discard lines to batch: batch source is stale and remapping failed.\n"
                    "File: {file}\n"
                    "Batch: {batch}\n"
                    "Error: {error}"
                ).format(
                    file=display_path(selection.file_path),
                    batch=batch_name,
                    error=str(e),
                )
            )

        snapshot_file_if_untracked(selection.file_path)
        add_source_bound_file_to_batch(
            batch_name,
            selection.file_path,
            bound_ownership,
            detect_file_mode(selection.file_path),
            batch_source_commit=batch_source_commit,
            expected_metadata_revision=metadata_revision,
        )


def _merge_replacement_with_batch(
    selection: DiscardLineReplacementSelection,
    *,
    file_metadata: BatchFileMetadataDict,
    batch_baseline_commit: str | None,
    original_working_lines: LineBuffer | None,
    ownership_stack: ExitStack,
) -> tuple[SourceBoundOwnership, str]:
    if not isinstance(batch_baseline_commit, str) or not batch_baseline_commit:
        raise ValueError("replacement update requires a batch baseline commit")

    current_batch_source = file_metadata.get("batch_source_commit")
    existing_ownership = ownership_stack.enter_context(
        acquire_ownership_for_metadata_dict(file_metadata)
    )
    old_source_buffer = read_git_object_buffer_or_none(
        f"{current_batch_source}:{selection.file_path}"
    )
    if old_source_buffer is None:
        exit_with_error(
            _(
                "Cannot discard lines to batch: failed to read batch source for '{file}'."
            ).format(file=display_path(selection.file_path))
        )

    reference_source_lines = ownership_stack.enter_context(
        read_git_object_buffer_or_empty(f"HEAD:{selection.file_path}")
    )
    reference_target_lines = ownership_stack.enter_context(
        read_git_object_buffer_or_empty(
            f"{batch_baseline_commit}:{selection.file_path}"
        )
    )
    with old_source_buffer as old_source_lines:
        old_bound_ownership = SourceBoundOwnership(
            content_snapshot(
                selection.file_path,
                old_source_lines,
                space=BatchSourceSpace,
            ),
            existing_ownership,
        )
        refreshed_complete = refresh_complete_source_replacement(
            old_source_lines,
            old_bound_ownership,
            rewritten_lines=selection.rewritten_working_lines,
            alternatives=selection.replacement_alternatives,
        )
        if refreshed_complete is not None:
            refreshed_complete = ownership_stack.enter_context(refreshed_complete)
            batch_source_commit = create_batch_source_commit(
                selection.file_path,
                file_buffer_override=refreshed_complete.source_buffer,
            )
            _record_session_batch_source(selection.file_path, batch_source_commit)
            return refreshed_complete.bound_ownership, batch_source_commit

        promoted_complete = promote_untracked_presence_to_complete_source_replacement(
            old_source_lines,
            old_bound_ownership,
            rewritten_lines=selection.rewritten_working_lines,
            alternatives=selection.replacement_alternatives,
        )
        if promoted_complete is not None:
            promoted_complete = ownership_stack.enter_context(promoted_complete)
            batch_source_commit = create_batch_source_commit(
                selection.file_path,
                file_buffer_override=promoted_complete.source_buffer,
            )
            _record_session_batch_source(selection.file_path, batch_source_commit)
            return promoted_complete.bound_ownership, batch_source_commit

        with (
            advance_source_lines_preserving_existing_presence(
                old_lines=old_source_lines,
                working_lines=selection.rewritten_working_lines,
                ownership=existing_ownership,
                advancing_working_ranges=(
                    _explicit_rewritten_working_ranges(selection)
                ),
                advancing_alternatives=selection.replacement_alternatives,
            ) as source_with_provenance,
        ):
            remapped_existing_ownership = remap_batch_ownership_with_lineage(
                ownership=existing_ownership,
                lineage=source_with_provenance.lineage,
            )
            with _acquire_rewritten_source_projection(
                selection,
                source_lines=source_with_provenance.source_buffer,
                transform=ExactLineageSourceCoordinates(source_with_provenance.lineage),
            ) as source_projection:
                origin_source_projection = (
                    BatchSourceExactTransform.from_rewritten_working_lineage(
                        selection.transformed_projection.rewritten_snapshot,
                        source_projection.source_snapshot,
                        source_with_provenance.lineage,
                    )
                )
                exact_alternative_range = _exact_alternative_source_range(
                    selection,
                    source_with_provenance.source_buffer,
                    translate_working_range=(
                        source_with_provenance.lineage.translate_working_range
                    ),
                )
                exact_prefix_range = _exact_owned_prefix_source_range(
                    selection,
                    source_with_provenance.source_buffer,
                    translate_working_range=(
                        source_with_provenance.lineage.translate_working_range
                    ),
                    following_source_range=exact_alternative_range,
                )
                alternatives = selection.replacement_alternatives
                if (
                    alternatives is not None
                    and alternatives.requires_exact_saved_presence
                    and (
                        exact_prefix_range is None
                        or (
                            _explicit_alternative_range(selection) is not None
                            and exact_alternative_range is None
                        )
                    )
                ):
                    raise ValueError(
                        "advanced batch source does not preserve the replacement "
                        "alternatives as contiguous ranges"
                    )
                new_ownership = _translate_rewritten_selection_ownership(
                    selection,
                    baseline_lines=reference_source_lines,
                    original_working_lines=original_working_lines,
                    rewritten_lines=selection.rewritten_working_lines,
                    exact_presence_range=exact_prefix_range,
                    source_projection=source_projection,
                    replacement_origin_source_projection=(origin_source_projection),
                )
                new_ownership = _expand_source_scoped_alternative_ownership(
                    new_ownership,
                    selection=selection,
                    source_line_count=len(source_with_provenance.source_buffer),
                    alternative_range=exact_alternative_range,
                    materialize_source_scope=False,
                )
                new_ownership = _refine_and_preserve_explicit_presence_span_boundary(
                    new_ownership,
                    selection=selection,
                    baseline_lines=reference_source_lines,
                    source_content_lines=source_with_provenance.source_buffer,
                    replacement_origin_source_projection=(origin_source_projection),
                )
            translate_ownership_baseline_references(
                new_ownership,
                reference_source_lines,
                reference_target_lines,
                replacement_origin_source_lines=reference_source_lines,
            )
            if exact_prefix_range is not None and exact_alternative_range is not None:
                new_ownership = _prepare_evolved_replacement_alternative(
                    new_ownership,
                    existing_ownership=remapped_existing_ownership,
                    saved_range=exact_prefix_range,
                )
                new_ownership = _add_explicit_source_alternative_replacement(
                    new_ownership,
                    selection=selection,
                    presence_range=exact_prefix_range,
                    alternative_range=exact_alternative_range,
                )
            batch_source_commit = create_batch_source_commit(
                selection.file_path,
                file_buffer_override=source_with_provenance.source_buffer,
            )
            _record_session_batch_source(selection.file_path, batch_source_commit)
            return (
                SourceBoundOwnership(
                    content_snapshot(
                        selection.file_path,
                        source_with_provenance.source_buffer,
                        space=BatchSourceSpace,
                    ),
                    merge_batch_ownership(
                        remapped_existing_ownership,
                        new_ownership,
                    ),
                ),
                batch_source_commit,
            )


def _record_session_batch_source(file_path: str, batch_source_commit: str) -> None:
    batch_sources = load_session_batch_sources()
    batch_sources[file_path] = batch_source_commit
    save_session_batch_sources(batch_sources)


def _explicit_rewritten_working_ranges(
    selection: DiscardLineReplacementSelection,
) -> LineRanges:
    """Return the edited lines in the rewritten file."""
    span = selection.transformed_projection.explicit_edit.rewritten_span
    if len(span) == 0:
        return LineRanges.empty()
    return LineRanges.from_ranges(((span.start.offset + 1, span.end.offset),))


@contextmanager
def _acquire_rewritten_source_projection(
    selection: DiscardLineReplacementSelection,
    *,
    source_lines: LineBuffer,
    transform: SourceCoordinateTransform,
) -> Iterator[SourceCoordinateProjection]:
    """Map each displayed row to its line in one batch source."""
    coordinate_lines = selection.rewritten_line_changes.lines
    source_snapshot = cast(
        FileSnapshot[BatchSourceSpace],
        content_snapshot(
            selection.file_path,
            source_lines,
            space=BatchSourceSpace,
        ),
    )

    def projected_pairs() -> Iterator[tuple[DisplayLineId, int | None]]:
        for line, source_line in translate_display_source_coordinates(
            coordinate_lines,
            transform,
        ):
            if line.id is None:
                continue
            yield DisplayLineId(line.id), source_line

    def resolve_anonymous_row(new_line_number: int | None) -> int | None:
        if new_line_number is None:
            return None
        return transform.translate_working_line(new_line_number)

    with SourceCoordinateProjection.from_pairs(
        view_identity=(
            selection.transformed_projection.ownership_selection.view.renderer_identity
        ),
        source_snapshot=source_snapshot,
        pairs=projected_pairs(),
        capacity=len(coordinate_lines),
        anonymous_row_resolver=resolve_anonymous_row,
    ) as projection:
        yield projection


def _translate_rewritten_selection_ownership(
    selection: DiscardLineReplacementSelection,
    *,
    baseline_lines: LineBuffer,
    original_working_lines: LineBuffer | None,
    rewritten_lines: LineBuffer,
    exact_presence_range: tuple[int, int] | None = None,
    source_projection: SourceCoordinateProjection,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Build batch claims for the selected rows from the full nearby diff."""
    source_projection.require_view(
        selection.transformed_projection.ownership_selection.view.renderer_identity
    )
    expanded_parents = _expanded_selected_replacement_parents(
        selection,
        baseline_lines=baseline_lines,
        original_working_lines=original_working_lines,
    )
    expanded_deletion_ids = LineRanges.from_ranges(
        range_pair
        for parent in expanded_parents
        for range_pair in parent.rewritten_deletion_ids.ranges()
    )
    selected_ids = selection.rewritten_selected_ids.difference(expanded_deletion_ids)
    replacement_runs = stream_replacement_line_runs_from_lines(
        old_file_lines=baseline_lines,
        new_file_lines=rewritten_lines,
    )
    ownership = translate_hunk_selection_to_batch_ownership(
        selection.rewritten_line_changes.lines,
        selected_ids,
        replacement_line_runs=replacement_runs,
        replacement_origin=SameStreamReplacementOrigin(baseline_lines),
        baseline_lines=baseline_lines,
        source_projection=source_projection,
        replacement_origin_source_projection=(replacement_origin_source_projection),
    )
    ownership = _add_expanded_replacement_parents(
        ownership,
        selection=selection,
        expanded_parents=expanded_parents,
        baseline_lines=baseline_lines,
        source_projection=source_projection,
        replacement_origin_source_projection=(replacement_origin_source_projection),
    )
    alternatives = selection.replacement_alternatives
    if (
        alternatives is not None
        and alternatives.requires_exact_saved_presence
        and (
            exact_presence_range is None
            or ownership.deletions
            or ownership.replacement_units
        )
    ):
        raise ValueError(
            "exact addition prefix did not translate to presence-only ownership"
        )
    if exact_presence_range is not None and (
        (alternatives is not None and alternatives.requires_exact_saved_presence)
        or (
            alternatives is None
            and not ownership.deletions
            and not ownership.replacement_units
        )
    ):
        exact_presence_lines = LineRanges.from_ranges((exact_presence_range,))
        if ownership.presence_line_set() != exact_presence_lines:
            ownership.presence_claims = presence_claims_from_source_lines(
                exact_presence_lines,
                ownership.presence_baseline_references(),
            )
    return ownership


def _expand_source_scoped_alternative_ownership(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    source_line_count: int,
    alternative_range: tuple[int, int] | None,
    materialize_source_scope: bool,
) -> BatchOwnership:
    """Select every source line except the version left in the worktree."""
    alternatives = selection.replacement_alternatives
    if (
        alternatives is None
        or not alternatives.owns_source_without_live
        or not materialize_source_scope
    ):
        return ownership
    if alternative_range is None:
        raise ValueError("source-scoped replacement has no live source range")
    if ownership.deletions or ownership.replacement_units:
        raise ValueError("source-scoped replacement did not translate to presence")
    alternative_start, alternative_end = alternative_range
    if not 1 <= alternative_start <= alternative_end <= source_line_count:
        raise ValueError("source-scoped replacement has an invalid live range")

    owned_builder = LineRangeBuilder()
    if alternative_start > 1:
        owned_builder.add_range(1, alternative_start - 1)
    if alternative_end < source_line_count:
        owned_builder.add_range(alternative_end + 1, source_line_count)
    ownership.presence_claims = presence_claims_from_source_lines(
        owned_builder.finish(),
        {},
    )
    return ownership


def _prepare_evolved_replacement_alternative(
    ownership: BatchOwnership,
    *,
    existing_ownership: BatchOwnership,
    saved_range: tuple[int, int],
) -> BatchOwnership:
    """Start a new link when the saved text is the prior live version.

    The existing unit already connects its saved text to this intermediate
    version.  The new unit only needs to connect the intermediate version to
    the text now left in the worktree.  Keeping the translated parent claims
    would join both links into one unit with two old sides.
    """
    saved_start, saved_end = saved_range
    for alternative in existing_ownership.resolve().replacement_alternatives:
        if len(alternative.live_payload) != 1:
            continue
        live_span = alternative.live_payload[0]
        if (
            live_span.start.offset + 1 != saved_start
            or live_span.end.offset != saved_end
        ):
            continue
        saved_lines = LineRanges.from_ranges((saved_range,))
        references = {
            source_line: reference
            for source_line, reference in ownership.presence_baseline_references().items()
            if source_line in saved_lines
        }
        return BatchOwnership(
            presence_claims=presence_claims_from_source_lines(
                saved_lines,
                references,
            ),
            deletions=[],
            replacement_units=[],
        )
    return ownership
