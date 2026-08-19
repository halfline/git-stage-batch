"""Text file batch persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .state.validation import get_validated_baseline_commit
from .state.compatibility_metadata import write_file_backed_batch_metadata
from .state.lifecycle import create_batch
from .state.query import get_batch_commit_sha, read_batch_metadata
from .state.metadata_types import BatchFileMetadataDict, add_ownership_metadata
from .state.batch_names import batch_exists, validate_batch_name
from ..core.text_lifecycle import (
    TextFileChangeType,
    normalized_text_change_type,
    resolve_text_change_type,
)
from .source.cache import (
    load_session_batch_sources,
    save_session_batch_sources,
)
from .source.snapshots import create_batch_source_commits
from ..core.buffer import LineBuffer
from ..core.coordinates import (
    BaselineSpace,
    BatchSourceSpace,
    content_snapshot,
    require_same_snapshot,
)
from ..utils.repository_buffers import (
    load_git_tree_files_as_buffers,
)
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
from .state import content_commits as _content_commits
from . import realized_file_content as _realized_file_content
from .file_state import (
    BatchFileState,
    BatchMetadataRevision,
    SourceBoundOwnership,
)

if TYPE_CHECKING:
    from .ownership.model import BatchOwnership


@dataclass(frozen=True)
class BatchFileUpdate:
    """One source-authoritative text file update to persist into a batch.

    The source commit and ownership binding are intentionally inseparable at
    this boundary.  Persistence validates the binding against the exact file
    stored by ``batch_source_commit`` instead of assigning authority to a raw
    ownership value supplied by its caller.
    """

    file_path: str
    batch_source_commit: str
    bound_ownership: SourceBoundOwnership
    expected_metadata_revision: BatchMetadataRevision
    file_mode: str = "100644"
    change_type: str | None = None

    def __post_init__(self) -> None:
        if type(self.file_path) is not str or not self.file_path:
            raise TypeError("batch file update path must be a non-empty string")
        if (
            type(self.batch_source_commit) is not str
            or not self.batch_source_commit
        ):
            raise ValueError("batch source commit must be non-empty")
        if not isinstance(self.bound_ownership, SourceBoundOwnership):
            raise TypeError("batch file update ownership must be source-bound")
        if not isinstance(
            self.expected_metadata_revision,
            BatchMetadataRevision,
        ):
            raise TypeError("batch file update revision must be typed")
        if self.bound_ownership.source_snapshot.path != self.file_path:
            raise ValueError("ownership source path does not match file update")

def add_source_bound_file_to_batch(
    batch_name: str,
    file_path: str,
    bound_ownership: SourceBoundOwnership,
    file_mode: str = "100644",
    *,
    batch_source_commit: str,
    expected_metadata_revision: BatchMetadataRevision,
    change_type: str | None = None,
) -> None:
    """Persist ownership already bound to an exact batch-source snapshot."""
    add_files_to_batch(
        batch_name,
        [
            BatchFileUpdate(
                file_path=file_path,
                batch_source_commit=batch_source_commit,
                bound_ownership=bound_ownership,
                expected_metadata_revision=expected_metadata_revision,
                file_mode=file_mode,
                change_type=change_type,
            )
        ],
    )


def add_file_to_batch(
    batch_name: str,
    file_path: str,
    ownership: 'BatchOwnership',
    file_mode: str = "100644",
    batch_source_commit: str | None = None,
    change_type: str | None = None,
) -> None:
    """Compatibility adapter for callers that still hold raw ownership.

    New domain code should retain ``SourceBoundOwnership`` from selection or
    transformation resolution and call ``add_source_bound_file_to_batch``.
    This legacy boundary resolves one source, validates its content, and binds
    the raw ownership explicitly before entering canonical persistence.

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
    batch_sources = load_session_batch_sources()
    resolved_source_commit = batch_source_commit or batch_sources.get(file_path)
    session_source_changed = False
    source_buffer: LineBuffer
    if resolved_source_commit is None:
        created_source = create_batch_source_commits([file_path])[file_path]
        resolved_source_commit = created_source.commit_sha
        source_buffer = created_source.file_buffer
        batch_sources[file_path] = resolved_source_commit
        session_source_changed = True
    else:
        loaded_sources = load_git_tree_files_as_buffers(
            resolved_source_commit,
            [file_path],
        )
        loaded_source_buffer = loaded_sources.get(file_path)
        source_buffer = (
            loaded_source_buffer
            if loaded_source_buffer is not None
            else LineBuffer.from_bytes(b"")
        )

    try:
        if not batch_exists(batch_name):
            create_batch(batch_name, "Auto-created")
        metadata = read_batch_metadata(batch_name)
        metadata_revision = metadata.get("revision")
        if not isinstance(metadata_revision, str) or not metadata_revision:
            raise ValueError("batch metadata has no durable revision")
        source_snapshot = content_snapshot(
            file_path,
            source_buffer,
            space=BatchSourceSpace,
        )
        add_source_bound_file_to_batch(
            batch_name,
            file_path,
            SourceBoundOwnership(source_snapshot, ownership),
            file_mode,
            batch_source_commit=resolved_source_commit,
            expected_metadata_revision=BatchMetadataRevision(metadata_revision),
            change_type=change_type,
        )
        if session_source_changed:
            save_session_batch_sources(batch_sources)
    finally:
        source_buffer.close()


def add_files_to_batch(batch_name: str, updates: list[BatchFileUpdate]) -> None:
    """Add or update text files in one batch content/state publication."""
    if not updates:
        return

    validate_batch_name(batch_name)

    batch_source_commits: dict[str, str] = {}
    batch_source_buffers: dict[str, LineBuffer] = {}
    managed_buffers: list[LineBuffer] = []

    def manage_buffers(
        buffers: dict[str, LineBuffer],
    ) -> dict[str, LineBuffer]:
        managed_buffers.extend(buffers.values())
        return buffers

    try:
        update_paths = [update.file_path for update in updates]
        if len(set(update_paths)) != len(update_paths):
            raise ValueError("batch file updates contain duplicate paths")
        for update in updates:
            batch_source_commits[update.file_path] = update.batch_source_commit

        expected_revision = updates[0].expected_metadata_revision
        if any(
            update.expected_metadata_revision != expected_revision
            for update in updates[1:]
        ):
            raise ValueError("batch file updates have different metadata revisions")

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

        # Validate source authority before auto-creating or otherwise mutating
        # the target batch. The same loaded buffers are retained for realization.
        for update in updates:
            batch_source_buffer = batch_source_buffers.get(
                update.file_path,
                empty_buffer,
            )
            require_same_snapshot(
                update.bound_ownership.source_snapshot,
                content_snapshot(
                    update.file_path,
                    batch_source_buffer,
                    space=BatchSourceSpace,
                ),
            )

        if not batch_exists(batch_name):
            raise ValueError("source-bound batch update requires an existing batch")

        baseline_commit = get_validated_baseline_commit(batch_name)
        metadata = read_batch_metadata(batch_name)
        if "files" not in metadata:
            metadata["files"] = {}
        metadata_revision = metadata.get("revision")
        if not isinstance(metadata_revision, str) or not metadata_revision:
            raise ValueError("batch metadata has no durable revision")
        if metadata_revision != expected_revision.value:
            raise ValueError(
                "batch metadata changed after ownership was prepared; retry "
                "against the latest batch state"
            )

        baseline_buffers = manage_buffers(
            load_git_tree_files_as_buffers(baseline_commit, update_paths)
        )

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

                source_snapshot = content_snapshot(
                    file_path,
                    batch_source_buffer,
                    space=BatchSourceSpace,
                )
                batch_file_state = BatchFileState(
                    path=file_path,
                    baseline_snapshot=content_snapshot(
                        file_path,
                        base_buffer,
                        space=BaselineSpace,
                    ),
                    source_snapshot=source_snapshot,
                    baseline_lines=base_buffer,
                    source_lines=batch_source_buffer,
                    bound_ownership=update.bound_ownership,
                    metadata_revision=expected_revision,
                )
                realized_buffer = _realized_file_content.build_realized_buffer(
                    batch_file_state
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

                file_metadata: BatchFileMetadataDict = {
                    "batch_source_commit": batch_source_commit,
                    "mode": update.file_mode,
                }
                existing_file_metadata = metadata["files"].get(file_path)
                if (
                    existing_file_metadata is not None
                    and existing_file_metadata.get(
                        "legacy_unmarked_source_alternatives"
                    ) is True
                ):
                    file_metadata[
                        "legacy_unmarked_source_alternatives"
                    ] = True
                add_ownership_metadata(
                    file_metadata,
                    update.bound_ownership.value.to_metadata_dict(),
                )
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

            metadata_model = write_file_backed_batch_metadata(
                batch_name,
                metadata,
            )
            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=_content_commits.batch_content_commit_parents(batch_name),
            message=f"Batch: {batch_name}",
        )

        from .state.references import sync_batch_state_refs
        sync_batch_state_refs(
            batch_name,
            metadata_model,
            content_commit=commit_sha,
            source_buffers=batch_source_buffers,
        )
    finally:
        for buffer in managed_buffers:
            buffer.close()
