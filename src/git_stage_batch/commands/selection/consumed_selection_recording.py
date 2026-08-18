"""Consumed-selection recording for include replacement commands."""

from __future__ import annotations

from collections.abc import Sequence

from ...batch.ownership.model import BatchOwnership
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.ownership.merging import merge_batch_ownership
from ...batch.ownership.translation import (
    translate_lines_to_batch_ownership,
)
from ...batch.state.metadata_types import (
    BatchFileMetadataDict,
    ReplacementMaskMetadata,
    add_ownership_metadata,
)
from ...batch.source.advancement import (
    BatchSourceAdvanceError,
    advance_batch_source_for_file_with_provenance,
)
from ...batch.source.selected_line_refresh import (
    refresh_selected_lines_against_new_source,
    refresh_selected_lines_against_source_lines,
)
from ...batch.source.refresh import map_selection_to_source
from ...core.buffer import LineBuffer
from ...core.models import LineEntry
from ...batch.source.snapshots import create_batch_source_commit
from ...utils.repository_buffers import read_git_object_buffer_or_none
from ...data.consumed_selections import (
    read_consumed_file_metadata,
    write_consumed_file_metadata,
)
from ...exceptions import CommandError
from ...git_paths import display_path
from ...i18n import _


def record_consumed_selection(
    file_path: str,
    *,
    source_buffer: LineBuffer,
    selected_lines: list[LineEntry],
    coordinate_lines: Sequence[LineEntry] | None = None,
    replacement_mask: ReplacementMaskMetadata | None = None,
) -> None:
    """Persist consumed selection ownership for masking across `again`."""
    existing_file_metadata = read_consumed_file_metadata(file_path)

    def persist_selection(
        *,
        batch_source_commit: str,
        ownership: BatchOwnership,
    ) -> None:
        file_metadata: BatchFileMetadataDict = {
            "batch_source_commit": batch_source_commit,
        }
        add_ownership_metadata(file_metadata, ownership.to_metadata_dict())
        existing_replacement_masks = (
            existing_file_metadata.get("replacement_masks", [])
            if existing_file_metadata else
            []
        )
        if replacement_mask is not None:
            replacement_masks = existing_replacement_masks[:]
            replacement_masks.append(replacement_mask)
            file_metadata["replacement_masks"] = replacement_masks
        elif existing_replacement_masks:
            file_metadata["replacement_masks"] = existing_replacement_masks
        write_consumed_file_metadata(file_path, file_metadata)

    if existing_file_metadata is not None:
        with acquire_ownership_for_metadata_dict(
            existing_file_metadata
        ) as existing_ownership:
            batch_source_commit = existing_file_metadata["batch_source_commit"]
            saved_source_buffer = read_git_object_buffer_or_none(
                f"{batch_source_commit}:{file_path}"
            )
            if saved_source_buffer is None:
                raise CommandError(
                    _(
                        "Cannot record the included replacement because "
                        "its saved source is unavailable.\n"
                        "File: {file}"
                    ).format(file=display_path(file_path))
                )
            with saved_source_buffer as saved_source_lines:
                mapped_selected_lines = map_selection_to_source(
                    selected_lines,
                    source_lines=saved_source_lines,
                    working_lines=source_buffer,
                    coordinate_lines=coordinate_lines,
                )
            if mapped_selected_lines is None:
                try:
                    with advance_batch_source_for_file_with_provenance(
                        batch_name="consumed-selections",
                        file_path=file_path,
                        old_batch_source_commit=batch_source_commit,
                        existing_ownership=existing_ownership,
                    ) as advance_result:
                        batch_source_commit = advance_result.batch_source_commit
                        existing_ownership = advance_result.ownership
                        selected_lines = refresh_selected_lines_against_source_lines(
                            selected_lines,
                            source_lines=advance_result.source_buffer,
                            working_lines=(),
                            exact_transforms=(
                                advance_result.source_transform,
                                advance_result.working_transform,
                            ),
                            coordinate_lines=coordinate_lines,
                        )
                except BatchSourceAdvanceError as error:
                    raise CommandError(
                        _(
                            "Cannot record the included replacement because "
                            "its saved source cannot be advanced.\n"
                            "File: {file}\n"
                            "Error: {error}"
                        ).format(file=display_path(file_path), error=error)
                    ) from error
            else:
                selected_lines = mapped_selected_lines
            new_ownership = translate_lines_to_batch_ownership(selected_lines)
            persist_selection(
                batch_source_commit=batch_source_commit,
                ownership=merge_batch_ownership(existing_ownership, new_ownership),
            )
            return
    else:
        mapped_selected_lines = map_selection_to_source(
            selected_lines,
            source_lines=source_buffer,
            working_lines=source_buffer,
            coordinate_lines=coordinate_lines,
        )
        if mapped_selected_lines is None:
            selected_lines = refresh_selected_lines_against_new_source(
                selected_lines,
                coordinate_lines=coordinate_lines,
            )
        else:
            selected_lines = mapped_selected_lines
        merged_ownership = translate_lines_to_batch_ownership(selected_lines)
        batch_source_commit = create_batch_source_commit(
            file_path,
            file_buffer_override=source_buffer,
        )

    persist_selection(
        batch_source_commit=batch_source_commit,
        ownership=merged_ownership,
    )
