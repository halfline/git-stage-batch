"""Consumed-selection recording for include replacement commands."""

from __future__ import annotations

from ...batch.ownership.model import BatchOwnership
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.ownership.merging import merge_batch_ownership
from ...batch.ownership.translation import (
    detect_stale_batch_source_for_selection,
    translate_lines_to_batch_ownership,
)
from ...batch.source.advancement import advance_batch_source_for_file_with_provenance
from ...batch.source.selected_line_refresh import (
    refresh_selected_lines_against_new_source,
    refresh_selected_lines_against_source_lines,
)
from ...core.buffer import LineBuffer
from ...batch.source.snapshots import create_batch_source_commit
from ...data.consumed_selections import (
    read_consumed_file_metadata,
    write_consumed_file_metadata,
)


def record_consumed_selection(
    file_path: str,
    *,
    source_buffer: LineBuffer,
    selected_lines: list,
    replacement_mask: dict[str, list[str]] | None = None,
) -> None:
    """Persist consumed selection ownership for masking across `again`."""
    existing_file_metadata = read_consumed_file_metadata(file_path)

    def persist_selection(
        *,
        batch_source_commit: str,
        ownership: BatchOwnership,
    ) -> None:
        file_metadata = {
            "batch_source_commit": batch_source_commit,
            **ownership.to_metadata_dict(),
        }
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
            if detect_stale_batch_source_for_selection(selected_lines):
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
                        lineage=advance_result.lineage,
                    )
            new_ownership = translate_lines_to_batch_ownership(selected_lines)
            persist_selection(
                batch_source_commit=batch_source_commit,
                ownership=merge_batch_ownership(existing_ownership, new_ownership),
            )
            return
    else:
        if detect_stale_batch_source_for_selection(selected_lines):
            selected_lines = refresh_selected_lines_against_new_source(selected_lines)
        merged_ownership = translate_lines_to_batch_ownership(selected_lines)
        batch_source_commit = create_batch_source_commit(
            file_path,
            file_buffer_override=source_buffer,
        )

    persist_selection(
        batch_source_commit=batch_source_commit,
        ownership=merged_ownership,
    )
