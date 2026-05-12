"""Batch storage operations: file management and diff generation."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .metadata_validation import get_validated_baseline_commit
from .operations import create_batch
from .query import get_batch_baseline_commit, get_batch_commit_sha, read_batch_metadata
from .state_refs import read_file_backed_batch_metadata
from .validation import batch_exists, validate_batch_name
from ..core.models import BinaryFileChange
from ..core.text_lifecycle import (
    TextFileChangeType,
    normalized_text_change_type,
    resolve_text_change_type,
)
from ..data.batch_sources import (
    create_batch_source_commit,
    create_batch_source_commits,
    get_batch_source_for_file,
    load_session_batch_sources,
    save_session_batch_sources,
)
from ..editor import (
    EditorBuffer,
    load_git_object_as_buffer,
    load_git_tree_files_as_buffers,
    restore_line_endings_in_chunks,
    detect_line_ending,
)
from ..utils.file_io import write_text_file_contents
from ..utils.git import (
    create_git_blob,
    GitIndexEntryUpdate,
    get_git_repository_root_path,
    git_commit_tree,
    git_read_tree,
    git_update_index,
    git_update_index_entries,
    git_write_tree,
    run_git_command,
    temp_git_index,
)
from ..utils.paths import get_batch_metadata_file_path
from ..utils.text import normalize_line_sequence_endings
from .merge import _satisfy_constraints

if TYPE_CHECKING:
    from .ownership import BatchOwnership, DeletionClaim


@dataclass(frozen=True)
class BatchFileUpdate:
    """One text file update to persist into a batch."""

    file_path: str
    ownership: BatchOwnership
    file_mode: str = "100644"
    batch_source_commit: str | None = None
    change_type: str | None = None


def add_file_to_batch(
    batch_name: str,
    file_path: str,
    ownership: 'BatchOwnership',
    file_mode: str = "100644",
    batch_source_commit: str | None = None,
    change_type: str | None = None,
) -> None:
    """Add or update a file in a batch using batch source-based storage.

    This stores the file's batch source commit (working tree at session start),
    claimed line ranges, and deletions in the batch metadata. It then builds
    realized content and updates the batch commit tree.

    Args:
        batch_name: Name of the batch
        file_path: Repository-relative path to the file
        ownership: BatchOwnership specifying claimed lines and deletions
        file_mode: Git file mode (default: 100644)
        batch_source_commit: Optional existing batch source commit to use.
            When omitted, uses the active session batch-source cache.
        change_type: Optional persisted text lifecycle type from another batch.
            Only whole-file added/deleted lifecycle states are retained.
    """
    add_files_to_batch(
        batch_name,
        [
            BatchFileUpdate(
                file_path=file_path,
                ownership=ownership,
                file_mode=file_mode,
                batch_source_commit=batch_source_commit,
                change_type=change_type,
            )
        ],
    )


def add_files_to_batch(batch_name: str, updates: list[BatchFileUpdate]) -> None:
    """Add or update text files in one batch content/state publication."""
    if not updates:
        return

    validate_batch_name(batch_name)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    baseline_commit = get_validated_baseline_commit(batch_name)
    metadata = read_batch_metadata(batch_name)
    if "files" not in metadata:
        metadata["files"] = {}

    batch_sources = load_session_batch_sources()
    batch_sources_changed = False
    batch_source_commits: dict[str, str] = {}
    batch_source_buffers: dict[str, EditorBuffer] = {}
    missing_source_paths: list[str] = []
    managed_buffers: list[EditorBuffer] = []

    def manage_buffers(
        buffers: dict[str, EditorBuffer],
    ) -> dict[str, EditorBuffer]:
        managed_buffers.extend(buffers.values())
        return buffers

    try:
        for update in updates:
            batch_source_commit = update.batch_source_commit or batch_sources.get(update.file_path)
            if batch_source_commit:
                batch_source_commits[update.file_path] = batch_source_commit
            else:
                missing_source_paths.append(update.file_path)

        created_sources = create_batch_source_commits(missing_source_paths)
        if created_sources:
            batch_sources_changed = True
            for file_path, source in created_sources.items():
                batch_sources[file_path] = source.commit_sha
                batch_source_commits[file_path] = source.commit_sha
                batch_source_buffers[file_path] = source.file_buffer
                managed_buffers.append(source.file_buffer)

        update_paths = [update.file_path for update in updates]
        baseline_buffers = manage_buffers(
            load_git_tree_files_as_buffers(baseline_commit, update_paths)
        )

        existing_source_paths_by_commit: dict[str, list[str]] = {}
        for update in updates:
            if update.file_path in batch_source_buffers:
                continue
            existing_source_paths_by_commit.setdefault(
                batch_source_commits[update.file_path],
                [],
            ).append(update.file_path)

        for source_commit, source_paths in existing_source_paths_by_commit.items():
            batch_source_buffers.update(
                manage_buffers(load_git_tree_files_as_buffers(source_commit, source_paths))
            )

        empty_buffer = EditorBuffer.from_bytes(b"")
        managed_buffers.append(empty_buffer)

        with temp_git_index() as env:
            existing_commit = get_batch_commit_sha(batch_name)
            if existing_commit:
                git_read_tree(existing_commit, env=env)

            index_updates: list[GitIndexEntryUpdate] = []
            realized_buffers: list[EditorBuffer] = []
            realized_buffer_indexes: list[int] = []
            for update in updates:
                file_path = update.file_path
                batch_source_commit = batch_source_commits[file_path]
                baseline_exists = file_path in baseline_buffers
                base_buffer = baseline_buffers.get(file_path, empty_buffer)
                batch_source_buffer = batch_source_buffers.get(file_path, empty_buffer)

                realized_buffer = _build_realized_buffer_from_lines(
                    base_buffer,
                    batch_source_buffer,
                    update.ownership,
                )
                managed_buffers.append(realized_buffer)

                requested_change_type = (
                    None if update.change_type is None else
                    normalized_text_change_type(update.change_type)
                )
                needs_source_content = (
                    not baseline_exists
                    and requested_change_type in (None, TextFileChangeType.ADDED)
                )
                text_change_type = resolve_text_change_type(
                    file_path=file_path,
                    baseline_exists=baseline_exists,
                    batch_source_content=(
                        batch_source_buffer
                        if needs_source_content else
                        b""
                    ),
                    realized_content=realized_buffer,
                    requested_change_type=update.change_type,
                )

                file_metadata = {
                    "batch_source_commit": batch_source_commit,
                    **update.ownership.to_metadata_dict(),
                    "mode": update.file_mode
                }
                if text_change_type != TextFileChangeType.MODIFIED:
                    file_metadata["change_type"] = text_change_type.value
                metadata["files"][file_path] = file_metadata

                if text_change_type == TextFileChangeType.DELETED:
                    index_updates.append(
                        GitIndexEntryUpdate(file_path=file_path, force_remove=True)
                    )
                else:
                    realized_buffer_indexes.append(len(index_updates))
                    realized_buffers.append(realized_buffer)
                    index_updates.append(
                        GitIndexEntryUpdate(
                            file_path=file_path,
                            mode=update.file_mode,
                        )
                    )

            blob_shas = [
                create_git_blob(buffer.byte_chunks())
                for buffer in realized_buffers
            ]
            for index_update_index, blob_sha in zip(
                realized_buffer_indexes,
                blob_shas,
                strict=True,
            ):
                index_update = index_updates[index_update_index]
                index_updates[index_update_index] = GitIndexEntryUpdate(
                    file_path=index_update.file_path,
                    mode=index_update.mode,
                    blob_sha=blob_sha,
                )
            git_update_index_entries(index_updates, env=env)

            metadata_path = get_batch_metadata_file_path(batch_name)
            write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))
            if batch_sources_changed:
                save_session_batch_sources(batch_sources)

            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=_batch_commit_parents(batch_name),
            message=f"Batch: {batch_name}",
        )

        from .state_refs import sync_batch_state_refs
        sync_batch_state_refs(
            batch_name,
            content_commit=commit_sha,
            source_buffers=batch_source_buffers,
        )
    finally:
        for buffer in managed_buffers:
            buffer.close()


def add_binary_file_to_batch(
    batch_name: str,
    binary_change: BinaryFileChange,
    file_mode: str = "100644",
    file_buffer_override: EditorBuffer | None = None,
) -> None:
    """Add a binary file change to a batch as an atomic unit.

    Binary files cannot have line-level operations, so they're stored
    as complete file changes (added, modified, or deleted).

    Args:
        batch_name: Name of the batch
        binary_change: BinaryFileChange describing the change
        file_mode: Git file mode (default: 100644)
        file_buffer_override: Optional buffer to persist for added/modified
            binary changes instead of reading the current working tree.
    """
    validate_batch_name(batch_name)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Determine file path
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path

    current_binary_buffer: EditorBuffer | None = None
    close_current_binary_buffer = False
    try:
        if binary_change.is_deleted_file():
            # Deleted binaries need the pre-delete source for baseline-style
            # operations, so reuse/create the normal session source.
            batch_source_commit = get_batch_source_for_file(file_path)
            if not batch_source_commit:
                batch_source_commit = create_batch_source_commit(file_path)
                batch_sources = load_session_batch_sources()
                batch_sources[file_path] = batch_source_commit
                save_session_batch_sources(batch_sources)
        else:
            # Added/modified binaries are atomic and cannot be reconstructed
            # from line ownership. Capture the current buffer as their source,
            # even when the session already has an older source commit for
            # this path.
            if file_buffer_override is None:
                full_path = get_git_repository_root_path() / file_path
                if not full_path.exists():
                    raise FileNotFoundError(file_path)
                current_binary_buffer = EditorBuffer.from_path(full_path)
                close_current_binary_buffer = True
            else:
                current_binary_buffer = file_buffer_override
            batch_source_commit = create_batch_source_commit(
                file_path,
                file_buffer_override=current_binary_buffer,
            )

        # For binary files, store the full live file bytes as the realized
        # content. Binary batches are atomic, so the batch commit must carry
        # the exact bytes the user is saving.
        if binary_change.is_deleted_file():
            # Deleted file: no content in batch (will be deleted when applied)
            blob_sha = None
        else:
            assert current_binary_buffer is not None
            blob_sha = create_git_blob(current_binary_buffer.byte_chunks())

        # Update batch metadata with binary file marker
        metadata = read_batch_metadata(batch_name)
        if "files" not in metadata:
            metadata["files"] = {}

        metadata["files"][file_path] = {
            "file_type": "binary",
            "change_type": binary_change.change_type,
            "batch_source_commit": batch_source_commit,
            "mode": file_mode
        }

        # Write updated metadata
        metadata_path = get_batch_metadata_file_path(batch_name)
        write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

        source_buffers = (
            {file_path: current_binary_buffer}
            if current_binary_buffer is not None else
            None
        )
        # Update batch commit tree
        if blob_sha:
            # Added or modified: add file to batch commit tree
            _update_batch_commit(
                batch_name,
                file_path,
                blob_sha,
                file_mode,
                source_buffers=source_buffers,
            )
        else:
            # Deleted: remove file from batch commit tree
            _remove_file_from_batch_commit(
                batch_name,
                file_path,
                source_buffers=source_buffers,
            )
    finally:
        if close_current_binary_buffer and current_binary_buffer is not None:
            current_binary_buffer.close()


def _build_realized_buffer_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
) -> EditorBuffer:
    """Build realized batch content as an editor buffer."""
    return EditorBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _stream_realized_content_chunks_from_lines(
                normalize_line_sequence_endings(base_lines),
                normalize_line_sequence_endings(batch_source_lines),
                ownership,
            ),
            detect_line_ending(batch_source_lines),
        )
    )


def _stream_realized_content_chunks_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
) -> Iterator[bytes]:
    """Yield realized batch content chunks from normalized line sequences."""
    # Resolve ownership
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    # Apply constraints using same model as merge, with lenient absence
    # enforcement because baseline may not have deletion content at that boundary.
    realized_entries = _satisfy_constraints(
        batch_source_lines,
        base_lines,
        presence_line_set,
        deletion_claims,
        strict=False
    )

    for entry in realized_entries:
        yield entry.content


def _suppress_sequence_at_position_bytes(
    lines: list[bytes],
    sequence: list[bytes],
    position: int
) -> list[bytes]:
    """Suppress exact byte sequence at specific position if it matches.

    This is position-specific, not global removal.

    Args:
        lines: File content split into lines
        sequence: Byte sequence to suppress
        position: 0-indexed position to check (sequence should start here)

    Returns:
        Lines with sequence suppressed if found at position, otherwise unchanged
    """
    if not sequence or not lines:
        return lines

    seq_len = len(sequence)

    # Check if position is valid
    if position < 0 or position >= len(lines):
        return lines

    # Check if sequence matches at this position
    if position + seq_len > len(lines):
        # Not enough lines remaining for sequence to match
        return lines

    # Check for exact match
    match = all(
        lines[position + j] == sequence[j]
        for j in range(seq_len)
    )

    if not match:
        # Sequence not found at this position - constraint already satisfied
        return lines

    # Suppress the sequence by removing it
    return lines[:position] + lines[position + seq_len:]


def _enforce_deletion_constraint(
    lines: list[bytes],
    claim: 'DeletionClaim'
) -> list[bytes]:
    """Remove sequences matching a deletion constraint.

    Scans the line list and removes any occurrence of the exact sequence
    specified by the deletion claim.

    Args:
        lines: Current content as list of lines (bytes with newlines)
        claim: Deletion constraint specifying content to suppress

    Returns:
        Content with matching sequences removed
    """
    if not claim.content_lines or not lines:
        return lines

    claim_length = len(claim.content_lines)
    result = []
    i = 0

    while i < len(lines):
        # Check if we have a match starting at position i
        if i + claim_length <= len(lines):
            match = all(
                lines[i + j] == claim.content_lines[j]
                for j in range(claim_length)
            )
            if match:
                # Skip this sequence (suppress it)
                i += claim_length
                continue

        # No match: keep this line
        result.append(lines[i])
        i += 1

    return result


def remove_file_from_batch(batch_name: str, file_path: str) -> None:
    """Remove a file from batch metadata and batch commit tree."""
    metadata = read_batch_metadata(batch_name)
    metadata.get("files", {}).pop(file_path, None)
    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))
    _remove_file_from_batch_commit(batch_name, file_path)


def copy_file_from_batch_to_batch(source_batch: str, dest_batch: str, file_path: str) -> None:
    """Copy one batch file's metadata and realized content into another batch."""
    source_metadata = read_batch_metadata(source_batch)
    file_meta = source_metadata.get("files", {}).get(file_path)
    if file_meta is None:
        raise KeyError(file_path)

    dest_metadata = read_batch_metadata(dest_batch)
    if "files" not in dest_metadata:
        dest_metadata["files"] = {}
    dest_metadata["files"][file_path] = deepcopy(file_meta)

    metadata_path = get_batch_metadata_file_path(dest_batch)
    write_text_file_contents(metadata_path, json.dumps(dest_metadata, indent=2))

    source_commit = get_batch_commit_sha(source_batch)
    if not source_commit:
        _remove_file_from_batch_commit(dest_batch, file_path)
        return

    source_buffer = load_git_object_as_buffer(f"{source_commit}:{file_path}")
    if source_buffer is not None:
        with source_buffer:
            blob_sha = create_git_blob(source_buffer.byte_chunks())
        file_mode = file_meta.get("mode", "100644")
        _update_batch_commit(dest_batch, file_path, blob_sha, file_mode)
    else:
        _remove_file_from_batch_commit(dest_batch, file_path)


def _batch_commit_parents(batch_name: str) -> list[str]:
    """Return parent commits for a batch content commit."""
    parents = []
    baseline = get_batch_baseline_commit(batch_name)
    if baseline:
        parents.append(baseline)

    metadata = read_file_backed_batch_metadata(batch_name)
    batch_source_commits = {
        file_meta["batch_source_commit"]
        for file_meta in metadata.get("files", {}).values()
        if "batch_source_commit" in file_meta
    }
    parents.extend(sorted(batch_source_commits))
    return parents


def _remove_file_from_batch_commit(
    batch_name: str,
    file_path: str,
    *,
    source_buffers: dict[str, EditorBuffer] | None = None,
) -> None:
    """Remove a file from batch commit tree (for deletions).

    Creates a new batch commit with the file removed from the tree.
    Used when a binary file deletion is stored in a batch.

    Args:
        batch_name: Name of the batch
        file_path: Repository-relative path to the file to remove
    """
    with temp_git_index() as env:
        existing_commit = get_batch_commit_sha(batch_name)
        if existing_commit:
            git_read_tree(existing_commit, env=env)

        # Remove file from the temporary index regardless of the working tree.
        # `--remove` consults worktree state and can leave a baseline blob in
        # the batch tree when the file exists locally; stored deletions need the
        # path absent from the batch commit unconditionally.
        git_update_index(file_path=file_path, force_remove=True, check=False, env=env)
        tree_sha = git_write_tree(env=env)

    commit_sha = git_commit_tree(
        tree_sha,
        parents=_batch_commit_parents(batch_name),
        message=f"Batch: {batch_name}",
    )

    from .state_refs import sync_batch_state_refs
    sync_batch_state_refs(
        batch_name,
        content_commit=commit_sha,
        source_buffers=source_buffers,
    )


def _update_batch_commit(
    batch_name: str,
    file_path: str,
    blob_sha: str,
    file_mode: str,
    *,
    source_buffers: dict[str, EditorBuffer] | None = None,
) -> None:
    """Update batch commit tree with new/updated file.

    Creates a new batch commit with parents=[baseline, ...batch sources].

    Args:
        batch_name: Name of the batch
        file_path: Repository-relative path to the file
        blob_sha: Blob SHA for the file content
        file_mode: File mode
    """
    with temp_git_index() as env:
        existing_commit = get_batch_commit_sha(batch_name)
        if existing_commit:
            git_read_tree(existing_commit, env=env)

        git_update_index(mode=file_mode, blob_sha=blob_sha, file_path=file_path, env=env)
        tree_sha = git_write_tree(env=env)

    commit_sha = git_commit_tree(
        tree_sha,
        parents=_batch_commit_parents(batch_name),
        message=f"Batch: {batch_name}",
    )

    from .state_refs import sync_batch_state_refs
    sync_batch_state_refs(
        batch_name,
        content_commit=commit_sha,
        source_buffers=source_buffers,
    )


def read_file_from_batch(batch_name: str, file_path: str) -> Optional[str]:
    """
    Read a file's content from a batch.

    Returns None if the batch doesn't exist or the file is not in the batch.
    """
    validate_batch_name(batch_name)

    commit_sha = get_batch_commit_sha(batch_name)
    if not commit_sha:
        return None

    # Use git show to read file from commit
    result = run_git_command(
        ["show", f"{commit_sha}:{file_path}"],
        check=False
    )
    if result.returncode != 0:
        return None

    return result.stdout


def get_batch_diff(batch_name: str, context_lines: int = 3) -> bytes:
    """
    Get the unified diff from baseline to batch.

    This shows what changes the batch represents. Returns empty bytes
    if baseline cannot be determined or batch doesn't exist.
    """
    validate_batch_name(batch_name)

    commit_sha = get_batch_commit_sha(batch_name)
    if not commit_sha:
        return b""

    baseline = get_batch_baseline_commit(batch_name)
    if not baseline:
        # No baseline, diff against empty tree
        empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        baseline = empty_tree

    # Generate diff as bytes
    result = run_git_command(
        ["diff", f"-U{context_lines}", baseline, commit_sha],
        check=False,
        text_output=False
    )
    if result.returncode != 0:
        return b""

    return result.stdout
