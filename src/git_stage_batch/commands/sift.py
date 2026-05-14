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
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from ..batch.comparison import (
    SemanticChangeKind,
    derive_semantic_change_runs,
)
from ..batch.merge import merge_batch_from_line_sequences_as_buffer
from ..exceptions import MergeError
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.operations import create_batch, delete_batch
from ..batch.ownership import BatchOwnership, DeletionClaim
from ..batch.query import get_batch_baseline_commit, read_batch_metadata
from ..batch.state_refs import (
    delete_batch_state_refs,
    get_batch_content_ref_name,
    sync_batch_state_refs,
)
from ..batch.storage import (
    add_binary_file_to_batch,
    _build_realized_buffer_from_lines,
    _remove_file_from_batch_commit,
    _update_batch_commit,
)
from ..batch.validation import batch_exists, validate_batch_name
from ..core.line_selection import LineRanges
from ..core.models import BinaryFileChange
from ..core.text_lifecycle import (
    TextFileChangeType,
    normalized_text_change_type,
    sifted_empty_text_path_change_type,
)
from ..editor import (
    EditorBuffer,
    buffer_byte_count,
    buffer_matches,
    load_git_object_as_buffer_or_empty,
    load_working_tree_file_as_buffer,
)
from ..exceptions import BatchMetadataError, exit_with_error
from ..i18n import _
from ..utils.text import normalize_line_sequence_endings
from ..utils.file_io import write_text_file_contents
from ..utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    git_commit_tree,
    git_read_tree,
    git_update_index,
    git_write_tree,
    require_git_repository,
    run_git_command,
    temp_git_index,
)
from ..utils.paths import (
    get_batch_metadata_file_path,
)


def create_synthetic_batch_source_commit(
    baseline_commit: str,
    file_path: str,
    file_buffer: EditorBuffer,
    file_mode: str = "100644",
) -> str:
    """Create a synthetic batch source commit for a single file.

    The created commit has ``baseline_commit`` as its parent, but the file at
    ``file_path`` contains ``file_buffer``. Sift uses this to persist target
    buffers for text files in a batch-source commit even when that content does
    not exist as-is in history.
    """
    blob_sha = create_git_blob(file_buffer.byte_chunks())

    with temp_git_index() as env:
        git_read_tree(baseline_commit, env=env)
        git_update_index(mode=file_mode, blob_sha=blob_sha, file_path=file_path, env=env)
        new_tree = git_write_tree(env=env)

    return git_commit_tree(
        new_tree,
        parents=[baseline_commit],
        message=f"Sift batch source for {file_path}",
    )



def add_sifted_text_file_to_batch(
    batch_name: str,
    file_path: str,
    target_buffer: EditorBuffer,
    ownership: BatchOwnership,
    file_mode: str = "100644",
    change_type: str | None = None,
) -> None:
    """Persist a sifted text file into a batch.

    ``target_buffer`` is the file content the sifted batch wants to realize
    when merged with an appropriate working tree. For sifted text files, the
    synthetic batch-source commit stores this target buffer directly, and the
    batch ref stores the same target buffer directly.

    The ownership is expressed in ``target_buffer`` coordinate space and is
    validated separately against the working tree before this helper is called.
    """
    validate_batch_name(batch_name)

    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    baseline_commit = get_batch_baseline_commit(batch_name)
    if not baseline_commit:
        exit_with_error(_("Batch '{name}' has no baseline commit").format(name=batch_name))

    batch_source_commit = create_synthetic_batch_source_commit(
        baseline_commit=baseline_commit,
        file_path=file_path,
        file_buffer=target_buffer,
        file_mode=file_mode,
    )

    target_blob_sha = create_git_blob(target_buffer.byte_chunks())

    metadata = read_batch_metadata(batch_name)
    if "files" not in metadata:
        metadata["files"] = {}

    text_change_type = normalized_text_change_type(change_type)
    file_metadata = {
        "batch_source_commit": batch_source_commit,
        **ownership.to_metadata_dict(),
        "mode": file_mode,
    }
    if text_change_type != TextFileChangeType.MODIFIED:
        file_metadata["change_type"] = text_change_type.value
    metadata["files"][file_path] = file_metadata

    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

    source_buffers = {file_path: target_buffer}
    if text_change_type == TextFileChangeType.DELETED:
        _remove_file_from_batch_commit(
            batch_name,
            file_path,
            source_buffers=source_buffers,
        )
    else:
        _update_batch_commit(
            batch_name,
            file_path,
            target_blob_sha,
            file_mode,
            source_buffers=source_buffers,
        )



def command_sift_batch(source_batch: str, dest_batch: str) -> None:
    """Sift a batch to remove portions already present at tip."""
    require_git_repository()
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
    retained_files = []

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
                result = _compute_sifted_binary_file(
                    source_batch,
                    file_path,
                    file_meta,
                    repo_root,
                )
            else:
                result = _compute_sifted_text_file(
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
                if result["type"] == "binary":
                    add_binary_file_to_batch(
                        dest_batch,
                        result["binary_change"],
                        file_mode=file_meta.get("mode", "100644"),
                        file_buffer_override=result.get("target_buffer"),
                    )
                else:
                    add_sifted_text_file_to_batch(
                        batch_name=dest_batch,
                        file_path=file_path,
                        target_buffer=result["target_buffer"],
                        ownership=result["ownership"],
                        file_mode=file_meta.get("mode", "100644"),
                        change_type=result.get("change_type"),
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
    retained_files: list,
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
            if result["type"] == "binary":
                add_binary_file_to_batch(
                    temp_batch_name,
                    result["binary_change"],
                    file_mode=file_meta.get("mode", "100644"),
                    file_buffer_override=result.get("target_buffer"),
                )
            else:
                add_sifted_text_file_to_batch(
                    batch_name=temp_batch_name,
                    file_path=file_path,
                    target_buffer=result["target_buffer"],
                    ownership=result["ownership"],
                    file_mode=file_meta.get("mode", "100644"),
                    change_type=result.get("change_type"),
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
    retained_files: list,
) -> dict[str, EditorBuffer]:
    """Return source buffers held by retained sift results."""
    source_buffers: dict[str, EditorBuffer] = {}
    for file_path, _file_meta, result in retained_files:
        target_buffer = _target_buffer_from_sift_result(result)
        if target_buffer is not None:
            source_buffers[file_path] = target_buffer
    return source_buffers


def _close_sifted_results(retained_files: list) -> None:
    """Close target buffers held by retained sift results."""
    for _file_path, _file_meta, result in retained_files:
        target_buffer = _target_buffer_from_sift_result(result)
        if target_buffer is not None:
            target_buffer.close()


def _target_buffer_from_sift_result(result: dict) -> EditorBuffer | None:
    target_buffer = result.get("target_buffer")
    if isinstance(target_buffer, EditorBuffer):
        return target_buffer
    return None



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



def _compute_sifted_binary_file(
    source_batch: str,
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> Optional[dict]:
    """Compute a sifted binary file result."""
    batch_source_commit = file_meta["batch_source_commit"]
    change_type = file_meta["change_type"]

    batch_source_buffer = load_git_object_as_buffer_or_empty(
        f"{batch_source_commit}:{file_path}"
    )

    full_path = repo_root / file_path
    working_exists = full_path.exists()
    working_buffer = (
        EditorBuffer.from_path(full_path)
        if working_exists else
        EditorBuffer.from_bytes(b"")
    )
    target_buffer: EditorBuffer | None = None
    try:
        if change_type == "deleted":
            if not working_exists:
                return None
        elif change_type in ("added", "modified"):
            if working_exists and buffer_matches(working_buffer, batch_source_buffer):
                return None
            target_buffer = batch_source_buffer
            batch_source_buffer = None

        old_path = file_path if change_type != "added" else "/dev/null"
        new_path = file_path if change_type != "deleted" else "/dev/null"

        result = {
            "type": "binary",
            "binary_change": BinaryFileChange(
                old_path=old_path,
                new_path=new_path,
                change_type=change_type,
            ),
        }
        if target_buffer is not None:
            result["target_buffer"] = target_buffer
            target_buffer = None
        return result
    finally:
        if batch_source_buffer is not None:
            batch_source_buffer.close()
        working_buffer.close()
        if target_buffer is not None:
            target_buffer.close()



def _compute_sifted_text_file(
    source_batch: str,
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> Optional[dict]:
    """Compute a sifted text file result.

    Returns a destination ownership in target-content coordinate space plus the
    target buffer itself.
    """
    batch_source_commit = file_meta["batch_source_commit"]
    change_type = normalized_text_change_type(file_meta.get("change_type"))
    baseline_commit = get_batch_baseline_commit(source_batch)
    full_path = repo_root / file_path
    working_exists = full_path.exists()

    batch_source_buffer = load_git_object_as_buffer_or_empty(
        f"{batch_source_commit}:{file_path}"
    )
    baseline_buffer = (
        load_git_object_as_buffer_or_empty(f"{baseline_commit}:{file_path}")
        if baseline_commit is not None else
        EditorBuffer.from_bytes(b"")
    )
    working_buffer = load_working_tree_file_as_buffer(file_path)
    target_buffer: EditorBuffer | None = None

    with (
        batch_source_buffer,
        baseline_buffer,
        working_buffer,
        BatchOwnership.acquire_for_metadata_dict(file_meta) as source_ownership,
    ):
        target_buffer = _build_realized_buffer_from_lines(
            baseline_buffer,
            batch_source_buffer,
            source_ownership,
        )
        try:
            target_exists = change_type != TextFileChangeType.DELETED
            if target_exists == working_exists and buffer_matches(
                working_buffer,
                target_buffer,
            ):
                return None

            working_lines = normalize_line_sequence_endings(working_buffer)
            target_lines = normalize_line_sequence_endings(target_buffer)

            new_ownership = build_ownership_from_working_and_target_lines(
                working_lines=working_lines,
                target_lines=target_lines,
            )
            if new_ownership is None or new_ownership.is_empty():
                result_change_type = sifted_empty_text_path_change_type(
                    change_type,
                    target_exists=target_exists,
                    working_exists=working_exists,
                    target_content=target_buffer,
                    ownership_is_empty=True,
                )
                if result_change_type == TextFileChangeType.MODIFIED:
                    return None
                new_ownership = BatchOwnership([], [])
            else:
                result_change_type = change_type

            validate_sifted_text_file_result_from_lines(
                target_lines=target_lines,
                dest_ownership=new_ownership,
                working_lines=working_lines,
            )

            returned_target_buffer = target_buffer
            target_buffer = None
            return {
                "type": "text",
                "ownership": new_ownership,
                "target_buffer": returned_target_buffer,
                "change_type": result_change_type.value,
            }
        finally:
            if target_buffer is not None:
                target_buffer.close()



def build_ownership_from_working_and_target_lines(
    working_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> Optional[BatchOwnership]:
    """Build ownership from normalized working and target byte-line sequences."""

    semantic_runs = derive_semantic_change_runs(
        source_lines=working_lines,
        target_lines=target_lines,
    )

    claimed_ranges: list[tuple[int, int]] = []
    deletion_claims = []

    for run in semantic_runs:
        if run.kind == SemanticChangeKind.PRESENCE:
            if run.target_start is not None and run.target_end is not None:
                claimed_ranges.append((run.target_start, run.target_end))
        elif run.kind == SemanticChangeKind.DELETION:
            if run.source_start is not None and run.source_end is not None:
                deletion_content = [
                    working_lines[index - 1]
                    for index in run.source_line_numbers()
                ]
                deletion_claims.append(
                    DeletionClaim(
                        anchor_line=run.target_anchor,
                        content_lines=deletion_content,
                    )
                )
        elif run.kind == SemanticChangeKind.REPLACEMENT:
            if run.source_start is not None and run.source_end is not None:
                deletion_content = [
                    working_lines[index - 1]
                    for index in run.source_line_numbers()
                ]
                deletion_claims.append(
                    DeletionClaim(
                        anchor_line=run.target_anchor,
                        content_lines=deletion_content,
                    )
                )
            if run.target_start is not None and run.target_end is not None:
                claimed_ranges.append((run.target_start, run.target_end))

    claimed_line_ranges = LineRanges.from_ranges(claimed_ranges)
    if not claimed_line_ranges and not deletion_claims:
        return None

    return BatchOwnership.from_presence_lines(
        claimed_line_ranges.to_range_strings(),
        deletion_claims,
    )


def validate_sifted_text_file_result_from_lines(
    target_lines: Sequence[bytes],
    dest_ownership: BatchOwnership,
    working_lines: Sequence[bytes],
) -> None:
    """Validate a sifted representation against normalized byte-line sequences."""
    resolved = dest_ownership.resolve()

    for claimed_line in resolved.presence_line_set:
        if claimed_line < 1 or claimed_line > len(target_lines):
            raise MergeError(
                f"Sift validation failed: claimed line {claimed_line} is out of bounds "
                f"(target content has {len(target_lines)} lines)"
            )

    for deletion_claim in resolved.deletion_claims:
        if deletion_claim.anchor_line is not None:
            if deletion_claim.anchor_line < 1 or deletion_claim.anchor_line > len(target_lines):
                raise MergeError(
                    f"Sift validation failed: deletion anchor {deletion_claim.anchor_line} "
                    f"is out of bounds (target content has {len(target_lines)} lines)"
                )
        if not deletion_claim.content_lines:
            raise MergeError("Sift validation failed: deletion claim has empty content")

    try:
        reconstructed_buffer = merge_batch_from_line_sequences_as_buffer(
            target_lines,
            dest_ownership,
            working_lines,
        )
    except MergeError as e:
        raise MergeError(
            f"Sift validation failed: destination representation cannot be merged: {e}"
        ) from e

    with reconstructed_buffer as reconstructed:
        if not buffer_matches(reconstructed, target_lines):
            target_byte_count = buffer_byte_count(target_lines)
            raise MergeError(
                f"Sift validation failed: applying destination representation does not produce "
                f"the expected target content. Expected {target_byte_count} bytes, got "
                f"{reconstructed.byte_count} bytes."
            )
