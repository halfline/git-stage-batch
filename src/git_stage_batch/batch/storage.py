"""Batch storage operations: file management."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .metadata_validation import get_validated_baseline_commit
from .operations import create_batch
from .query import get_batch_commit_sha, read_batch_metadata
from .validation import batch_exists, validate_batch_name
from ..core.text_lifecycle import (
    TextFileChangeType,
    normalized_text_change_type,
    resolve_text_change_type,
)
from ..data.batch_sources import (
    create_batch_source_commits,
    load_session_batch_sources,
    save_session_batch_sources,
)
from ..core.buffer import LineBuffer
from ..utils.repository_buffers import (
    load_git_tree_files_as_buffers,
)
from ..utils.file_io import write_text_file_contents
from ..utils.git_index import (
    GitIndexEntryUpdate,
    git_commit_tree,
    git_read_tree,
    git_update_index_entries,
    git_write_tree,
    temp_git_index,
)
from ..utils.git_repository import get_git_repository_root_path
from ..utils.git_object_io import create_git_blob
from ..utils.paths import get_batch_metadata_file_path
from . import content_commits as _content_commits
from . import realized_file_content as _realized_file_content

if TYPE_CHECKING:
    from .ownership import BatchOwnership


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
    batch_source_buffers: dict[str, LineBuffer] = {}
    missing_source_paths: list[str] = []
    managed_buffers: list[LineBuffer] = []

    def manage_buffers(
        buffers: dict[str, LineBuffer],
    ) -> dict[str, LineBuffer]:
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

        empty_buffer = LineBuffer.from_bytes(b"")
        managed_buffers.append(empty_buffer)

        with temp_git_index() as env:
            existing_commit = get_batch_commit_sha(batch_name)
            if existing_commit:
                git_read_tree(existing_commit, env=env)

            index_updates: list[GitIndexEntryUpdate] = []
            realized_buffers: list[LineBuffer] = []
            realized_buffer_indexes: list[int] = []
            for update in updates:
                file_path = update.file_path
                batch_source_commit = batch_source_commits[file_path]
                baseline_exists = file_path in baseline_buffers
                base_buffer = baseline_buffers.get(file_path, empty_buffer)
                batch_source_buffer = batch_source_buffers.get(file_path, empty_buffer)

                realized_buffer = (
                    _realized_file_content.build_realized_buffer_from_lines(
                        base_buffer,
                        batch_source_buffer,
                        update.ownership,
                    )
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
                    working_exists=(
                        get_git_repository_root_path() / file_path
                    ).exists(),
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
            parents=_content_commits.batch_content_commit_parents(batch_name),
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
