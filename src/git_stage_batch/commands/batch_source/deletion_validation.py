"""Protect later edits when a saved deletion has no rename relationship."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
import hashlib

from ...batch.discard import discard_batch_from_line_sequences_as_buffer
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...batch.state.validation import get_validated_baseline_commit
from ...core.buffer import LineBuffer
from ...core.text_lines import normalize_line_sequence_endings
from ...data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identity,
)
from ...exceptions import CommandError
from ...git_paths import display_path
from ...i18n import _
from ...utils.file_job_workspace import FileJobWorkspace
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    load_git_blob_as_buffer,
)


def require_unchanged_deletion_targets(
    batch_name: str,
    files: dict[str, BatchFileMetadataDict],
    worktree_identities: dict[str, WorktreeIdentity],
    *,
    workspace: FileJobWorkspace,
    index_identities: dict[str, IndexIdentity] | None = None,
) -> None:
    """Refuse unlinked whole-file deletions whose captured target has evolved."""
    deleted_paths = [
        path
        for path, metadata in files.items()
        if metadata.get("change_type") == "deleted"
        and metadata.get("file_type") in {None, "binary"}
        and "rename_to" not in metadata
    ]
    if not deleted_paths:
        return
    baseline = get_validated_baseline_commit(batch_name)
    for ordinal, path in enumerate(deleted_paths):
        metadata = files[path]
        is_binary = metadata.get("file_type") == "binary"
        with _acquire_deletion_versions(baseline, path, metadata) as (
            baseline_lines, preimage,
        ):
            working = worktree_identities[path]
            changed = False
            if working.exists:
                if is_binary:
                    changed = working.digest != _byte_digest(preimage)
                else:
                    artifact = workspace.artifact_path(ordinal, "deletion-worktree")
                    identity = capture_worktree_identity(
                        path, content_artifact_path=artifact,
                    )
                    # Normalized content is only evidence for deletion ownership.
                    # Publication still requires the exact captured identity.
                    changed = identity != working
                    with workspace.read_buffer(artifact) as buffer:
                        changed = changed or (
                            normalize_line_sequence_endings(buffer)
                            != normalize_line_sequence_endings(preimage)
                        )
            if index_identities is not None:
                indexed_oid = index_identities[path].content_object_id
                if indexed_oid is not None:
                    with load_git_blob_as_buffer(indexed_oid) as buffer:
                        changed = changed or (
                            _byte_digest(buffer) != _byte_digest(preimage)
                            if is_binary else
                            not _index_matches_saved_deletion(
                                buffer, baseline_lines, preimage,
                            )
                        )
        if changed:
            raise CommandError(
                _(
                    "Cannot delete {file}: it has changed since the batch was saved."
                ).format(
                    file=display_path(path),
                )
            )


@contextmanager
def _acquire_deletion_versions(
    baseline_commit: str,
    path: str,
    metadata: BatchFileMetadataDict,
) -> Iterator[tuple[LineBuffer, LineBuffer]]:
    """Acquire the original baseline and the deletion's recorded preimage."""
    with ExitStack() as stack:
        baseline = read_git_object_buffer_or_none(f"{baseline_commit}:{path}")
        if metadata.get("file_type") == "binary":
            if baseline is None:
                raise CommandError(
                    _("Batch source content is missing for {file}.").format(
                        file=display_path(path),
                    )
                )
            baseline_lines = stack.enter_context(baseline)
            yield baseline_lines, baseline_lines
            return
        baseline_lines = stack.enter_context(
            baseline if baseline is not None else LineBuffer.from_bytes(b"")
        )
        source = read_git_object_buffer_or_none(
            f"{metadata['batch_source_commit']}:{path}"
        )
        source_lines = stack.enter_context(
            source if source is not None else LineBuffer.from_bytes(b"")
        )
        ownership = stack.enter_context(acquire_ownership_for_metadata_dict(metadata))
        preimage = stack.enter_context(discard_batch_from_line_sequences_as_buffer(
            source_lines, ownership, (), baseline_lines,
        ))
        yield baseline_lines, preimage


def _byte_digest(buffer: LineBuffer) -> str:
    digest = hashlib.sha256()
    for chunk in buffer.byte_chunks():
        digest.update(chunk)
    return digest.hexdigest()


def _index_matches_saved_deletion(
    indexed_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    preimage_lines: Sequence[bytes],
) -> bool:
    """Accept the original index or recorded removed lines, in order."""
    indexed = normalize_line_sequence_endings(indexed_lines)
    # Sift can capture worktree replacements without staging them. The batch
    # still deletes the original path, whose index may retain its baseline.
    if indexed == normalize_line_sequence_endings(baseline_lines):
        return True
    preimage = normalize_line_sequence_endings(preimage_lines)
    # Captured worktree additions may also be only partly staged. Every index
    # line must belong to the recorded deletion; do not admit unrelated edits.
    index_position = 0
    for line in preimage:
        if index_position < len(indexed) and indexed[index_position] == line:
            index_position += 1
    return index_position == len(indexed)
