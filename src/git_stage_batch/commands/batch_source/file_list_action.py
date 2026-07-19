"""Show-from multi-file list action orchestration."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...batch.atomic_file_changes import (
    binary_change_from_batch_file_metadata,
    gitlink_change_from_batch_file_metadata,
)
from ...batch.file_display import build_batch_file_display_from_inputs
from ...batch.ownership.metadata_blobs import (
    deletion_content_blob_ids,
    deletion_reference_blob_ids,
    presence_claim_reference_blob_ids,
    replacement_origin_reference_blob_ids,
)
from ...batch.ownership.metadata_loading import ownership_from_metadata_dict
from ...core.models import FileModeChange
from ...data.file_review.records import ReviewSource
from ...data.selected_change.clear_reasons import (
    mark_selected_change_cleared_by_file_list,
)
from ...data.selected_change.lifecycle import clear_selected_change_state_files
from ...exceptions import RepositoryDataInvalid
from ...i18n import _
from ...output.file_review_list import (
    make_binary_file_review_list_entry,
    make_file_review_list_entry,
    make_gitlink_file_review_list_entry,
    make_mode_file_review_list_entry,
    print_file_review_list,
)
from ...utils.git_object_io import resolve_git_objects
from ...utils.repository_buffers import (
    acquire_git_blob_buffers,
    git_object_name_is_batch_protocol_safe,
    read_git_object_buffer_or_none,
)


def show_batch_source_file_list(
    *,
    batch_name: str,
    files: dict[str, dict],
    selectable: bool,
    command_source_args: str,
) -> None:
    """Show a navigational list for multiple files from a batch."""
    entries = []
    text_files = {
        file_path: file_meta
        for file_path, file_meta in files.items()
        if file_meta.get("file_type") not in {"binary", "gitlink", "mode"}
    }
    source_name_by_path = {
        file_path: f"{file_meta['batch_source_commit']}:{file_path}"
        for file_path, file_meta in text_files.items()
    }
    deletion_ids = list(
        dict.fromkeys(
            blob_id
            for file_meta in text_files.values()
            for blob_id in deletion_content_blob_ids(file_meta.get("deletions", []))
        )
    )
    reference_ids = list(
        dict.fromkeys(
            blob_id
            for file_meta in text_files.values()
            for blob_id in [
                *deletion_reference_blob_ids(file_meta.get("deletions", [])),
                *presence_claim_reference_blob_ids(
                    file_meta.get("presence_claims", [])
                ),
                *replacement_origin_reference_blob_ids(
                    file_meta.get("replacement_units", [])
                ),
            ]
        )
    )
    batch_source_names = [
        source_name
        for source_name in source_name_by_path.values()
        if git_object_name_is_batch_protocol_safe(source_name)
    ]
    object_info_by_name = resolve_git_objects(
        [*batch_source_names, *deletion_ids, *reference_ids]
    )
    for source_name in batch_source_names:
        object_info = object_info_by_name.get(source_name)
        if object_info is not None and object_info.object_type != "blob":
            raise RepositoryDataInvalid(
                f"Git object {source_name!r} is {object_info.object_type}, not a blob"
            )
    _require_ownership_blobs(
        text_files=text_files,
        object_info_by_name=object_info_by_name,
    )
    object_id_by_name = {
        name: info.object_id
        for name, info in object_info_by_name.items()
        if info.object_type == "blob"
    }
    object_ids = list(dict.fromkeys(object_id_by_name.values()))

    with ExitStack() as source_buffers:
        source_buffer_by_path = {}
        unsafe_source_paths = [
            file_path
            for file_path, source_name in source_name_by_path.items()
            if not git_object_name_is_batch_protocol_safe(source_name)
        ]
        for file_path in unsafe_source_paths:
            source_name = source_name_by_path[file_path]
            source_buffer = read_git_object_buffer_or_none(source_name)
            if source_buffer is not None:
                source_buffer_by_path[file_path] = source_buffers.enter_context(
                    source_buffer
                )

        with acquire_git_blob_buffers(object_ids) as buffer_by_id:
            for file_path, source_name in source_name_by_path.items():
                source_object_id = object_id_by_name.get(source_name)
                if source_object_id is not None:
                    source_buffer_by_path[file_path] = buffer_by_id[source_object_id]
            reference_contents = {
                blob_id: buffer_by_id[object_id_by_name[blob_id]].to_bytes()
                for blob_id in reference_ids
                if blob_id in object_id_by_name
            }
            deletion_buffers = {
                blob_id: buffer_by_id[object_id_by_name[blob_id]]
                for blob_id in deletion_ids
                if blob_id in object_id_by_name
            }
            _append_batch_file_entries(
                entries=entries,
                files=files,
                source_buffer_by_path=source_buffer_by_path,
                reference_contents=reference_contents,
                deletion_buffers=deletion_buffers,
            )

    if entries:
        if selectable:
            clear_selected_change_state_files()
            mark_selected_change_cleared_by_file_list(
                source=ReviewSource.BATCH.value,
                batch_name=batch_name,
            )
        print_file_review_list(
            source_label=_("Changes: batch {name}").format(name=batch_name),
            entries=entries,
            command_source_args=command_source_args,
        )
    else:
        print(_("Batch '{name}' is empty").format(name=batch_name), file=sys.stderr)


def _require_ownership_blobs(
    *,
    text_files: dict[str, dict],
    object_info_by_name: dict,
) -> None:
    """Require every blob referenced by ownership metadata to be readable."""
    for file_path, file_meta in text_files.items():
        required_ids = [
            *deletion_content_blob_ids(file_meta.get("deletions", [])),
            *deletion_reference_blob_ids(file_meta.get("deletions", [])),
            *presence_claim_reference_blob_ids(file_meta.get("presence_claims", [])),
            *replacement_origin_reference_blob_ids(
                file_meta.get("replacement_units", [])
            ),
        ]
        for object_id in dict.fromkeys(required_ids):
            object_info = object_info_by_name.get(object_id)
            if object_info is None:
                raise RepositoryDataInvalid(
                    f"Git object {object_id!r} required by batch metadata for "
                    f"{file_path!r} is missing"
                )
            if object_info.object_type != "blob":
                raise RepositoryDataInvalid(
                    f"Git object {object_id!r} required by batch metadata for "
                    f"{file_path!r} is {object_info.object_type}, not a blob"
                )


def _append_batch_file_entries(
    *,
    entries: list,
    files: dict[str, dict],
    source_buffer_by_path: dict,
    reference_contents: dict[str, bytes],
    deletion_buffers: dict,
) -> None:
    """Append ordered batch entries from caller-owned bulk object inputs."""
    for file_path, file_meta in files.items():
        if file_meta.get("file_type") == "mode":
            entries.append(
                make_mode_file_review_list_entry(
                    FileModeChange(
                        file_path,
                        file_meta["old_mode"],
                        file_meta["new_mode"],
                    )
                )
            )
            continue
        binary_change = binary_change_from_batch_file_metadata(file_path, file_meta)
        if binary_change is not None:
            entries.append(make_binary_file_review_list_entry(binary_change))
            continue
        gitlink_change = gitlink_change_from_batch_file_metadata(file_path, file_meta)
        if gitlink_change is not None:
            entries.append(make_gitlink_file_review_list_entry(gitlink_change))
            continue
        source_buffer = source_buffer_by_path.get(file_path)
        if source_buffer is None:
            continue
        ownership = ownership_from_metadata_dict(
            file_meta,
            blob_contents=reference_contents,
            deletion_blob_buffers=deletion_buffers,
        )
        rendered = build_batch_file_display_from_inputs(
            file_path=file_path,
            file_meta=file_meta,
            ownership=ownership,
            batch_source_lines=source_buffer,
            probe_mergeability=False,
        )
        if rendered is not None:
            entries.append(
                make_file_review_list_entry(
                    rendered.line_changes,
                )
            )
