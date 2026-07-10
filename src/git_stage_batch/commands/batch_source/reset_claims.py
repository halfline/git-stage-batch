"""Claim mutation helpers for reset-from-batch."""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from contextlib import AbstractContextManager

from ...batch.lifecycle import create_batch
from ...batch.ownership import BatchOwnership
from ...batch.ownership_detachment import acquire_detached_batch_ownership
from ...batch.ownership_metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.ownership_merging import merge_batch_ownership
from ...batch.ownership_units import (
    build_ownership_units_from_batch_source_lines,
)
from ...batch.ownership_unit_rebuild import rebuild_ownership_from_units
from ...batch.ownership_unit_selection import filter_ownership_units_by_display_ids
from ...batch.ownership_unit_validation import validate_ownership_units
from ...batch.query import read_batch_metadata
from ...batch.selection import require_display_ids_available
from ...batch.state_refs import sync_batch_state_refs
from ...batch.text_file_storage import (
    add_file_to_batch,
)
from ...batch.file_entry_storage import (
    copy_file_from_batch_to_batch,
    remove_file_from_batch,
)
from ...batch.submodule_pointer import (
    is_batch_submodule_pointer,
    refuse_batch_submodule_pointer_lines,
)
from ...batch.validation import batch_exists
from ...core.line_selection import LineRanges
from ...data.batch_file_scope import resolve_batch_file_scope
from ...utils.repository_buffers import load_git_object_as_buffer
from ...exceptions import MergeError, exit_with_error
from ...i18n import _
from ...utils.file_io import write_text_file_contents
from ...utils.paths import get_batch_metadata_file_path


def move_claims_between_batches(
    source_batch: str,
    dest_batch: str,
    file: str | None,
    patterns: list[str] | None,
    selected_line_ids: LineRanges | None,
) -> None:
    """Move selected claims from one batch to another."""
    source_metadata = read_batch_metadata(source_batch)
    files = resolve_batch_file_scope(
        source_batch,
        source_metadata.get("files", {}),
        file,
        patterns,
    )
    _ensure_destination_batch(source_batch, dest_batch, source_metadata)

    if selected_line_ids is not None:
        file_path = list(files.keys())[0]
        with _acquire_line_ownership_for_file(
            source_batch,
            file_path,
            selected_line_ids,
        ) as selected_ownership:
            _add_ownership_to_destination(
                dest_batch,
                file_path,
                source_metadata["files"][file_path],
                selected_ownership,
            )
            reset_line_claims_for_file(source_batch, file_path, selected_line_ids)
        return

    for file_path, file_meta in files.items():
        if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(
            file_meta
        ):
            dest_file_meta = (
                read_batch_metadata(dest_batch).get("files", {}).get(file_path)
            )
            if dest_file_meta is not None:
                exit_with_error(
                    _("Destination batch already has file '{file}'").format(
                        file=file_path,
                    )
                )
            copy_file_from_batch_to_batch(source_batch, dest_batch, file_path)
        else:
            with acquire_ownership_for_metadata_dict(file_meta) as ownership:
                _add_ownership_to_destination(
                    dest_batch,
                    file_path,
                    file_meta,
                    ownership,
                )
        remove_file_from_batch(source_batch, file_path)


def reset_file_claims_from_batch(
    batch_name: str,
    file: str,
    selected_line_ids: LineRanges | None = None,
) -> None:
    """Remove claims for a file, or selected line claims within that file."""
    metadata = read_batch_metadata(batch_name)
    files = resolve_batch_file_scope(batch_name, metadata.get("files", {}), file)

    if selected_line_ids is None:
        file_path = list(files.keys())[0]
        remove_file_from_batch(batch_name, file_path)
        return

    file_path = list(files.keys())[0]
    reset_line_claims_for_file(batch_name, file_path, selected_line_ids)


def reset_pattern_claims_from_batch(
    batch_name: str,
    patterns: list[str],
    selected_line_ids: LineRanges | None = None,
) -> None:
    """Remove claims for files selected by gitignore-style patterns."""
    metadata = read_batch_metadata(batch_name)
    files = resolve_batch_file_scope(
        batch_name,
        metadata.get("files", {}),
        None,
        patterns,
    )

    if selected_line_ids is None:
        for file_path in files:
            remove_file_from_batch(batch_name, file_path)
        return

    file_path = list(files.keys())[0]
    reset_line_claims_for_file(batch_name, file_path, selected_line_ids)


def reset_line_claims_from_batch(
    batch_name: str,
    selected_line_ids: LineRanges,
) -> None:
    """Remove specific line claims from a batch."""
    metadata = read_batch_metadata(batch_name)
    files = resolve_batch_file_scope(batch_name, metadata.get("files", {}), None)
    file_path = list(files.keys())[0]
    reset_line_claims_for_file(batch_name, file_path, selected_line_ids)


def reset_line_claims_for_file(
    batch_name: str,
    file_path: str,
    lines_to_remove: LineRanges,
) -> None:
    """Remove specific display line IDs from one batch file."""
    metadata = read_batch_metadata(batch_name)
    file_meta = metadata["files"][file_path]

    if file_meta.get("file_type") == "binary":
        exit_with_error(
            _("Cannot use --lines with binary files. Reset the whole file instead.")
        )
    if is_batch_submodule_pointer(file_meta):
        refuse_batch_submodule_pointer_lines(_("Reset"))

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(
        f"{batch_source_commit}:{file_path}"
    )
    if batch_source_buffer is None:
        exit_with_error(
            _("Failed to read batch source content for {file}").format(
                file=file_path,
            )
        )

    with (
        acquire_ownership_for_metadata_dict(file_meta) as ownership,
        batch_source_buffer as batch_source_lines,
    ):
        remaining_units = partition_line_ownership_units(
            ownership,
            batch_source_lines,
            lines_to_remove,
            batch_name=batch_name,
            file_path=file_path,
        )[0]
        validate_ownership_units(remaining_units)
        new_ownership = rebuild_ownership_from_units(remaining_units)

        if new_ownership.is_empty():
            remove_file_from_batch(batch_name, file_path)
            return

        file_mode = file_meta.get("mode", "100644")
        add_file_to_batch(
            batch_name,
            file_path,
            new_ownership,
            file_mode,
            batch_source_commit=batch_source_commit,
            change_type=file_meta.get("change_type"),
        )


def partition_line_ownership_units(
    ownership: BatchOwnership,
    batch_source_lines: Sequence[bytes],
    selected_line_ids: LineRanges,
    *,
    batch_name: str,
    file_path: str,
):
    """Partition ownership units by selected display line IDs."""
    units = build_ownership_units_from_batch_source_lines(
        ownership,
        batch_source_lines,
    )
    available_ids = LineRanges.from_ranges(
        display_id_range
        for unit in units
        for display_id_range in unit.display_line_ids.ranges()
    )
    require_display_ids_available(
        selected_line_ids,
        available_ids,
        line_id_specification=selected_line_ids.to_line_spec(),
        file_path=file_path,
        review_command=(
            "git-stage-batch show --from "
            f"{shlex.quote(batch_name)} --file {shlex.quote(file_path)}"
        ),
    )

    try:
        return filter_ownership_units_by_display_ids(
            units,
            selected_line_ids,
        )
    except MergeError as e:
        exit_with_error(str(e))


def reset_all_claims_from_batch(batch_name: str) -> None:
    """Remove all claims from a batch."""
    metadata = read_batch_metadata(batch_name)
    metadata["files"] = {}
    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))
    sync_batch_state_refs(batch_name)


def _ensure_destination_batch(
    source_batch: str,
    dest_batch: str,
    source_metadata: dict,
) -> None:
    """Create destination batch from source baseline, or verify compatibility."""
    source_baseline = source_metadata.get("baseline")

    if batch_exists(dest_batch):
        dest_metadata = read_batch_metadata(dest_batch)
        if dest_metadata.get("baseline") != source_baseline:
            exit_with_error(
                _(
                    "Destination batch '{dest}' has a different baseline from "
                    "source batch '{source}'"
                ).format(
                    dest=dest_batch,
                    source=source_batch,
                )
            )
        return

    create_batch(
        dest_batch,
        note=_("Split from {source}").format(source=source_batch),
        baseline_commit=source_baseline,
    )


def _add_ownership_to_destination(
    dest_batch: str,
    file_path: str,
    source_file_meta: dict,
    ownership: BatchOwnership,
) -> None:
    """Add selected text ownership to destination, merging with compatible claims."""
    dest_metadata = read_batch_metadata(dest_batch)
    dest_file_meta = dest_metadata.get("files", {}).get(file_path)
    batch_source_commit = source_file_meta["batch_source_commit"]

    def add_to_destination(destination_ownership: BatchOwnership) -> None:
        file_mode = source_file_meta.get("mode", "100644")
        add_file_to_batch(
            dest_batch,
            file_path,
            destination_ownership,
            file_mode,
            batch_source_commit=batch_source_commit,
            change_type=source_file_meta.get("change_type"),
        )

    if dest_file_meta is not None:
        if dest_file_meta.get("file_type") == "binary":
            exit_with_error(
                _(
                    "Destination batch already has a binary version of '{file}', "
                    "so text changes for the same file cannot be moved there."
                ).format(
                    file=file_path,
                )
            )
        if dest_file_meta.get("batch_source_commit") != batch_source_commit:
            exit_with_error(
                _(
                    "Destination batch already has file '{file}' with a "
                    "different batch source"
                ).format(
                    file=file_path,
                )
            )
        with acquire_ownership_for_metadata_dict(dest_file_meta) as existing:
            add_to_destination(merge_batch_ownership(existing, ownership))
        return

    add_to_destination(ownership)


def _acquire_line_ownership_for_file(
    batch_name: str,
    file_path: str,
    lines_to_select: LineRanges,
) -> AbstractContextManager[BatchOwnership]:
    """Acquire ownership for selected display line IDs from one batch file."""
    metadata = read_batch_metadata(batch_name)
    file_meta = metadata["files"][file_path]

    if file_meta.get("file_type") == "binary":
        exit_with_error(
            _("Cannot use --lines with binary files. Reset the whole file instead.")
        )
    if is_batch_submodule_pointer(file_meta):
        refuse_batch_submodule_pointer_lines(_("Reset"))

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(
        f"{batch_source_commit}:{file_path}"
    )
    if batch_source_buffer is None:
        exit_with_error(
            _("Failed to read batch source content for {file}").format(
                file=file_path,
            )
        )

    with (
        acquire_ownership_for_metadata_dict(file_meta) as ownership,
        batch_source_buffer as batch_source_lines,
    ):
        _remaining_units, selected_units = partition_line_ownership_units(
            ownership,
            batch_source_lines,
            lines_to_select,
            batch_name=batch_name,
            file_path=file_path,
        )
        validate_ownership_units(selected_units)
        return acquire_detached_batch_ownership(
            rebuild_ownership_from_units(selected_units)
        )
