"""Batch ownership update preparation for selected lines."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from ..core.models import LineEntry
from .ownership.hunk_translation import translate_hunk_selection_to_batch_ownership
from .ownership.model import BatchOwnership
from .ownership.metadata_loading import acquire_ownership_for_metadata_dict
from .ownership.merging import merge_batch_ownership
from .ownership.translation import translate_lines_to_batch_ownership
from .ownership.replacement_line_runs import ReplacementLineRun
from .merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
)
from .source.refresh import (
    RefreshedBatchSelection,
    ensure_batch_source_current_for_selection,
    prepare_initial_batch_source_for_selection,
)
from ..utils.repository_buffers import read_git_object_buffer_or_empty


@dataclass
class PreparedBatchUpdate:
    """Prepared ownership update for a batch file after stale-source handling.

    This represents a complete ownership update ready to be persisted,
    including the new ownership merged with existing ownership.
    """
    batch_source_commit: str | None
    """The batch source commit to use for this file."""

    ownership_before: BatchOwnership | None
    """Ownership before applying this update, possibly remapped to new source."""

    ownership_after: BatchOwnership
    """Ownership after merging new selection with existing ownership."""


def _merge_refreshed_selected_lines_into_hunk(
    hunk_lines: Sequence[LineEntry],
    selected_lines: Sequence[LineEntry],
) -> list[LineEntry]:
    """Return full hunk lines with refreshed selected-line coordinates."""
    selected_by_id = {
        line.id: line
        for line in selected_lines
        if line.id is not None
    }
    if not selected_by_id:
        return list(hunk_lines)

    return [
        selected_by_id.get(line.id, line)
        if line.id is not None else
        line
        for line in hunk_lines
    ]


def _translate_selection_to_batch_ownership(
    selected_lines: list,
    *,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
) -> BatchOwnership:
    """Translate a selection, using full-hunk replacement context when available."""
    selected_ids = {
        line.id
        for line in selected_lines
        if line.id is not None
    }
    if hunk_lines is not None and replacement_line_runs is not None and selected_ids:
        return translate_hunk_selection_to_batch_ownership(
            _merge_refreshed_selected_lines_into_hunk(
                hunk_lines,
                selected_lines,
            ),
            selected_ids,
            replacement_line_runs=replacement_line_runs,
            replacement_origin_line_runs=replacement_origin_line_runs,
            replacement_origin_source_lines=(
                replacement_origin_source_lines
                if replacement_origin_line_runs is not None
                else None
            ),
        )

    return translate_lines_to_batch_ownership(selected_lines)


def prepare_batch_ownership_update_for_selection(
    batch_name: str,
    file_path: str,
    current_batch_source_commit: str | None,
    existing_ownership: BatchOwnership | None,
    selected_lines: list,
    *,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_line_runs: Iterable[ReplacementLineRun] | None = None,
    reference_source_lines: Sequence[bytes] | None = None,
    reference_target_lines: Sequence[bytes] | None = None,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
) -> PreparedBatchUpdate:
    """Prepare complete ownership update after stale-source handling."""
    refreshed = ensure_batch_source_current_for_selection(
        batch_name=batch_name,
        file_path=file_path,
        current_batch_source_commit=current_batch_source_commit,
        existing_ownership=existing_ownership,
        selected_lines=selected_lines,
        coordinate_lines=hunk_lines,
    )
    return _prepare_batch_ownership_update_from_refreshed_selection(
        refreshed,
        hunk_lines=hunk_lines,
        replacement_line_runs=replacement_line_runs,
        replacement_origin_line_runs=replacement_origin_line_runs,
        reference_source_lines=reference_source_lines,
        reference_target_lines=reference_target_lines,
        replacement_origin_source_lines=replacement_origin_source_lines,
    )


def _prepare_batch_ownership_update_from_refreshed_selection(
    refreshed: RefreshedBatchSelection,
    *,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_line_runs: Iterable[ReplacementLineRun] | None = None,
    reference_source_lines: Sequence[bytes] | None = None,
    reference_target_lines: Sequence[bytes] | None = None,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
) -> PreparedBatchUpdate:
    """Translate and merge a selection whose source is already established."""

    new_ownership = _translate_selection_to_batch_ownership(
        refreshed.selected_lines,
        hunk_lines=hunk_lines,
        replacement_line_runs=replacement_line_runs,
        replacement_origin_line_runs=replacement_origin_line_runs,
        replacement_origin_source_lines=replacement_origin_source_lines,
    )
    if (reference_source_lines is None) != (reference_target_lines is None):
        raise ValueError(
            "reference source and target lines must be provided together"
        )
    if (
        replacement_origin_source_lines is not None
        and reference_target_lines is None
    ):
        raise ValueError(
            "replacement origin source lines require reference target lines"
        )
    if reference_source_lines is not None and reference_target_lines is not None:
        translate_ownership_baseline_references(
            new_ownership,
            reference_source_lines,
            reference_target_lines,
            replacement_origin_source_lines=replacement_origin_source_lines,
        )

    if refreshed.ownership:
        merged_ownership = merge_batch_ownership(refreshed.ownership, new_ownership)
    else:
        merged_ownership = new_ownership

    return PreparedBatchUpdate(
        batch_source_commit=refreshed.batch_source_commit,
        ownership_before=refreshed.ownership,
        ownership_after=merged_ownership
    )


@contextmanager
def acquire_batch_ownership_update_for_selection(
    *,
    batch_name: str,
    file_path: str,
    file_metadata: dict | None,
    selected_lines: list,
    initial_batch_source_commit: str | None = None,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_line_runs: Iterable[ReplacementLineRun] | None = None,
    reference_source_lines: Sequence[bytes] | None = None,
    batch_baseline_commit: str | None = None,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
) -> Iterator[PreparedBatchUpdate]:
    """Acquire existing ownership metadata while preparing a batch update.

    The yielded ownership may borrow deletion content from acquired metadata,
    so callers should persist or detach it before leaving the context.
    """
    with ExitStack() as stack:
        if file_metadata is None:
            if initial_batch_source_commit is None:
                current_batch_source_commit, prepared_selected_lines = (
                    prepare_initial_batch_source_for_selection(
                        file_path,
                        selected_lines,
                        coordinate_lines=hunk_lines,
                    )
                )
            else:
                current_batch_source_commit = initial_batch_source_commit
                prepared_selected_lines = selected_lines
            existing_ownership = None
            refreshed = RefreshedBatchSelection(
                batch_source_commit=current_batch_source_commit,
                ownership=existing_ownership,
                selected_lines=prepared_selected_lines,
                source_was_advanced=False,
            )
        else:
            current_batch_source_commit = file_metadata.get(
                "batch_source_commit"
            )
            existing_ownership = stack.enter_context(
                acquire_ownership_for_metadata_dict(file_metadata)
            )
            refreshed = ensure_batch_source_current_for_selection(
                batch_name=batch_name,
                file_path=file_path,
                current_batch_source_commit=current_batch_source_commit,
                existing_ownership=existing_ownership,
                selected_lines=selected_lines,
                coordinate_lines=hunk_lines,
            )

        def prepare_update(
            reference_target_lines: Sequence[bytes] | None,
        ) -> PreparedBatchUpdate:
            return _prepare_batch_ownership_update_from_refreshed_selection(
                refreshed,
                hunk_lines=hunk_lines,
                replacement_line_runs=replacement_line_runs,
                replacement_origin_line_runs=replacement_origin_line_runs,
                reference_source_lines=reference_source_lines,
                reference_target_lines=reference_target_lines,
                replacement_origin_source_lines=replacement_origin_source_lines,
            )

        if reference_source_lines is None:
            update = prepare_update(None)
        else:
            if not isinstance(batch_baseline_commit, str) or not (
                batch_baseline_commit
            ):
                raise ValueError(
                    "selection baseline lines require a batch baseline commit"
                )
            with read_git_object_buffer_or_empty(
                f"{batch_baseline_commit}:{file_path}"
            ) as reference_target_lines:
                update = prepare_update(reference_target_lines)

        yield update
