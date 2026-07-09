"""Sift batch command: remove already-present portions from a batch.

For text files, sift derives a new destination batch whose source content is the
*target* file content the original batch wanted to realize, and whose ownership
represents the remaining delta needed to merge that target with the current
working tree.

That means sifted text batches intentionally use slightly different persistence
semantics than ordinary text batches:

- the synthetic batch source commit stores the target file content directly
- the batch ref for that file also stores that same target file content directly
- the ownership describes how to merge that target with the current working tree

This is deliberate. Validation still proves the real semantic invariant:
merging the destination representation against the current working tree must
produce the intended target content.
"""

from __future__ import annotations

import json
import sys

from ..exceptions import MergeError
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.operations import create_batch, delete_batch
from ..batch.query import read_batch_metadata
from ..batch.state_refs import (
    delete_batch_state_refs,
    get_batch_content_ref_name,
    sync_batch_state_refs,
)
from ..batch.storage import add_binary_file_to_batch
from ..batch.validation import batch_exists, validate_batch_name
from ..batch.source_selector import require_plain_batch_name
from .batch_transform import sift_persistence as _sift_persistence
from .batch_transform import sift_results as _sift_results
from ..core.buffer import LineBuffer
from ..exceptions import BatchMetadataError, exit_with_error
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git import (
    get_git_repository_root_path,
    require_git_repository,
    run_git_command,
)
from ..utils.paths import (
    get_batch_metadata_file_path,
)


_RetainedSiftedFile = tuple[str, dict, _sift_results.SiftedFileResult]


def command_sift_batch(source_batch: str, dest_batch: str) -> None:
    """Sift a batch to remove portions already present at tip."""
    require_git_repository()
    source_batch = require_plain_batch_name(source_batch, "sift")
    dest_batch = require_plain_batch_name(dest_batch, "sift")
    validate_batch_name(source_batch)
    validate_batch_name(dest_batch)

    if not batch_exists(source_batch):
        exit_with_error(_("Batch '{name}' does not exist").format(name=source_batch))

    try:
        source_metadata = read_validated_batch_metadata(source_batch)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    source_files = source_metadata.get("files", {})
    if not source_files:
        _handle_empty_source_batch(source_batch, dest_batch)
        return

    in_place = source_batch == dest_batch
    dest_created = False
    retained_files: list[_RetainedSiftedFile] = []

    if not in_place:
        if batch_exists(dest_batch):
            exit_with_error(
                _(
                    "Destination batch '{name}' already exists. "
                    "Drop it first or use --to {source} for in-place sift."
                ).format(
                    name=dest_batch,
                    source=source_batch,
                )
            )

        create_batch(
            dest_batch,
            note=f"Sifted from {source_batch}",
            baseline_commit=source_metadata.get("baseline"),
        )
        dest_created = True

    try:
        repo_root = get_git_repository_root_path()
        stats = {
            "total_files": len(source_files),
            "files_removed": 0,
            "files_retained": 0,
        }

        for file_path, file_meta in source_files.items():
            is_binary = file_meta.get("file_type") == "binary"

            if is_binary:
                result = _sift_results.compute_sifted_binary_file(
                    file_path,
                    file_meta,
                    repo_root,
                )
            else:
                result = _sift_results.compute_sifted_text_file(
                    source_batch,
                    file_path,
                    file_meta,
                    repo_root,
                )

            if result is None:
                stats["files_removed"] += 1
            else:
                stats["files_retained"] += 1
                retained_files.append((file_path, file_meta, result))

        if in_place:
            _perform_atomic_in_place_sift(
                batch_name=source_batch,
                retained_files=retained_files,
                source_metadata=source_metadata,
            )
        else:
            for file_path, file_meta, result in retained_files:
                if isinstance(result, _sift_results.SiftedBinaryFileResult):
                    add_binary_file_to_batch(
                        dest_batch,
                        result.binary_change,
                        file_mode=file_meta.get("mode", "100644"),
                        file_buffer_override=result.target_buffer,
                    )
                else:
                    _sift_persistence.add_sifted_text_file_to_batch(
                        batch_name=dest_batch,
                        file_path=file_path,
                        target_buffer=result.target_buffer,
                        ownership=result.ownership,
                        file_mode=file_meta.get("mode", "100644"),
                        change_type=result.change_type,
                    )
    except MergeError as e:
        if dest_created and batch_exists(dest_batch):
            delete_batch(dest_batch)
        exit_with_error(
            _("Could not sift batch '{source}': {error}").format(
                source=source_batch,
                error=e,
            )
        )
    finally:
        _close_sifted_results(retained_files)

    if stats["files_retained"] == 0:
        if in_place:
            print(
                _(
                    "✓ Sifted batch '{name}' in-place: all content already present at tip (batch now empty)"
                ).format(name=source_batch),
                file=sys.stderr,
            )
        else:
            print(
                _(
                    "✓ Sifted batch '{source}' to '{dest}': all content already present at tip (destination empty)"
                ).format(
                    source=source_batch,
                    dest=dest_batch,
                ),
                file=sys.stderr,
            )
    else:
        if in_place:
            print(
                _(
                    "✓ Sifted batch '{name}' in-place: {retained} of {total} files still need changes"
                ).format(
                    name=source_batch,
                    retained=stats["files_retained"],
                    total=stats["total_files"],
                ),
                file=sys.stderr,
            )
        else:
            print(
                _(
                    "✓ Sifted batch '{source}' to '{dest}': {retained} of {total} files still need changes"
                ).format(
                    source=source_batch,
                    dest=dest_batch,
                    retained=stats["files_retained"],
                    total=stats["total_files"],
                ),
                file=sys.stderr,
            )


def _perform_atomic_in_place_sift(
    batch_name: str,
    retained_files: list[_RetainedSiftedFile],
    source_metadata: dict,
) -> None:
    """Perform atomic in-place sift by writing to a temp batch then replacing."""
    temp_batch_name = f"{batch_name}-sift-temp"

    if batch_exists(temp_batch_name):
        delete_batch(temp_batch_name)

    create_batch(
        temp_batch_name,
        note=f"Temporary sift of {batch_name}",
        baseline_commit=source_metadata.get("baseline"),
    )

    try:
        for file_path, file_meta, result in retained_files:
            if isinstance(result, _sift_results.SiftedBinaryFileResult):
                add_binary_file_to_batch(
                    temp_batch_name,
                    result.binary_change,
                    file_mode=file_meta.get("mode", "100644"),
                    file_buffer_override=result.target_buffer,
                )
            else:
                _sift_persistence.add_sifted_text_file_to_batch(
                    batch_name=temp_batch_name,
                    file_path=file_path,
                    target_buffer=result.target_buffer,
                    ownership=result.ownership,
                    file_mode=file_meta.get("mode", "100644"),
                    change_type=result.change_type,
                )

        temp_commit = run_git_command(
            ["rev-parse", get_batch_content_ref_name(temp_batch_name)],
            check=False,
            requires_index_lock=False,
        )
        if temp_commit.returncode == 0:
            commit_sha = temp_commit.stdout.strip()
            temp_metadata = read_batch_metadata(temp_batch_name)
            metadata_path = get_batch_metadata_file_path(batch_name)
            write_text_file_contents(metadata_path, json.dumps(temp_metadata, indent=2))
            sync_batch_state_refs(
                batch_name,
                content_commit=commit_sha,
                source_buffers=_source_buffers_from_sift_results(retained_files),
            )

        delete_batch_state_refs(temp_batch_name)

    except Exception:
        if batch_exists(temp_batch_name):
            delete_batch(temp_batch_name)
        raise


def _source_buffers_from_sift_results(
    retained_files: list[_RetainedSiftedFile],
) -> dict[str, LineBuffer]:
    """Return source buffers held by retained sift results."""
    source_buffers: dict[str, LineBuffer] = {}
    for file_path, _file_meta, result in retained_files:
        target_buffer = _target_buffer_from_sift_result(result)
        if target_buffer is not None:
            source_buffers[file_path] = target_buffer
    return source_buffers


def _close_sifted_results(retained_files: list[_RetainedSiftedFile]) -> None:
    """Close target buffers held by retained sift results."""
    for _file_path, _file_meta, result in retained_files:
        result.close()


def _target_buffer_from_sift_result(
    result: _sift_results.SiftedFileResult,
) -> LineBuffer | None:
    return result.target_source_buffer()


def _handle_empty_source_batch(source_batch: str, dest_batch: str) -> None:
    """Handle the case where the source batch is empty."""
    if source_batch == dest_batch:
        print(_("Batch '{name}' is already empty").format(name=source_batch), file=sys.stderr)
        return

    source_metadata = read_batch_metadata(source_batch)
    create_batch(
        dest_batch,
        note=f"Sifted from {source_batch} (was empty)",
        baseline_commit=source_metadata.get("baseline"),
    )

    print(
        _("✓ Sifted batch '{source}' to '{dest}': source was empty").format(
            source=source_batch,
            dest=dest_batch,
        ),
        file=sys.stderr,
    )
