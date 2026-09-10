"""Plan saved renames from captured source and destination contents."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import filecmp
from pathlib import Path

from .action_plans import close_resources
from ...batch.renames import require_complete_rename_selection
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...core.buffer import LineBuffer
from ...core.text_lifecycle import TextFileChangeType
from ...core.text_lines import normalize_line_sequence_endings
from ...data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identity,
    read_index_identities,
)
from ...editor.line_endings import choose_line_ending, restore_line_endings_in_chunks
from ...exceptions import CommandError
from ...git_paths import display_path
from ...i18n import _
from ...utils.file_job_workspace import FileJobWorkspace
from ...utils.git_command import run_git_command
from ...utils.git_object_io import create_git_blob
from ...utils.repository_buffers import (
    load_git_blob_as_buffer,
)


@dataclass
class RenameFileTarget:
    file_path: str
    buffer: LineBuffer | None
    file_mode: str | None
    change_type: TextFileChangeType

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass
class RenamePlans:
    worktree_targets: dict[str, RenameFileTarget]
    index_targets: dict[str, RenameFileTarget]
    worktree_identities: dict[str, WorktreeIdentity]
    index_identities: dict[str, IndexIdentity]

    def close(self) -> None:
        close_resources([*self.worktree_targets.values(), *self.index_targets.values()])


def is_rename_file(metadata: BatchFileMetadataDict) -> bool:
    return "rename_from" in metadata or "rename_to" in metadata


def prepare_rename_plans(
    files: dict[str, BatchFileMetadataDict],
    *,
    workspace: FileJobWorkspace,
    include_index: bool = False,
    reverse: bool = False,
) -> RenamePlans:
    """Merge both paths before publication, retaining all mutation identities."""
    files = {path: meta for path, meta in files.items() if is_rename_file(meta)}
    require_complete_rename_selection(files)
    result = RenamePlans({}, {}, {}, {})
    if not files:
        return result
    result.index_identities = read_index_identities(files)
    artifacts: dict[str, Path] = {}
    try:
        for ordinal, path in enumerate(files):
            artifact = workspace.artifact_path(ordinal, "rename-worktree")
            identity = capture_worktree_identity(path, content_artifact_path=artifact)
            if identity.kind not in {"regular", "missing"}:
                raise CommandError(
                    _(
                        "Cannot replay a saved rename over a non-regular path: {file}."
                    ).format(
                        file=display_path(path),
                    )
                )
            if result.index_identities[path].unmerged_entries:
                raise CommandError(
                    _(
                        "Cannot replay a saved rename with unmerged index entries: {file}."
                    ).format(
                        file=display_path(path),
                    )
                )
            result.worktree_identities[path] = identity
            artifacts[path] = artifact

        for ordinal, (new_path, metadata) in enumerate(files.items()):
            old_path = metadata.get("rename_from")
            if old_path is None:
                continue
            with ExitStack() as stack:
                base = stack.enter_context(
                    load_git_blob_as_buffer(metadata["rename_base_blob"])
                )
                saved = stack.enter_context(
                    load_git_blob_as_buffer(metadata["rename_target_blob"])
                )
                base_path = workspace.write_buffer(ordinal, "rename-base", base)
                saved_path = workspace.write_buffer(ordinal, "rename-saved", saved)
            source, destination = (
                (new_path, old_path) if reverse else (old_path, new_path)
            )
            before, after = (
                (saved_path, base_path) if reverse else (base_path, saved_path)
            )
            base_mode = files[source].get("mode", "100644")
            saved_mode = files[destination].get("mode", "100644")
            is_binary = any(
                files[path].get("file_type") == "binary"
                for path in (source, destination)
            )
            worktree_modes = {
                path: (
                    "100755"
                    if (result.worktree_identities[path].mode or 0) & 0o111
                    else "100644"
                )
                for path in (source, destination)
            }
            result.worktree_targets.update(
                _merge_pair(
                    source,
                    destination,
                    existing={
                        path: result.worktree_identities[path].exists
                        for path in (source, destination)
                    },
                    artifacts=artifacts,
                    modes=worktree_modes,
                    before=before,
                    after=after,
                    base_mode=base_mode,
                    saved_mode=saved_mode,
                    is_binary=is_binary,
                    ordinal=ordinal,
                    workspace=workspace,
                )
            )
            if include_index:
                index_artifacts: dict[str, Path] = {}
                for path in (source, destination):
                    index_identity = result.index_identities[path]
                    if index_identity.content_object_id is not None:
                        if index_identity.mode not in {"100644", "100755"}:
                            raise CommandError(
                                _(
                                    "Cannot replay a saved rename over a non-regular index entry."
                                )
                            )
                        with load_git_blob_as_buffer(
                            index_identity.content_object_id
                        ) as buffer:
                            index_artifacts[path] = workspace.write_buffer(
                                ordinal, "rename-index", buffer
                            )
                result.index_targets.update(
                    _merge_pair(
                        source,
                        destination,
                        existing={
                            path: path in index_artifacts
                            for path in (source, destination)
                        },
                        artifacts=index_artifacts,
                        modes={
                            path: result.index_identities[path].mode or "100644"
                            for path in (source, destination)
                        },
                        before=before,
                        after=after,
                        base_mode=base_mode,
                        saved_mode=saved_mode,
                        is_binary=is_binary,
                        for_index=True,
                        ordinal=ordinal,
                        workspace=workspace,
                    )
                )
        return result
    except BaseException:
        result.close()
        raise


def _merge_pair(
    source: str,
    destination: str,
    *,
    existing: dict[str, bool],
    artifacts: dict[str, Path],
    modes: dict[str, str],
    before: Path,
    after: Path,
    base_mode: str,
    saved_mode: str,
    is_binary: bool,
    ordinal: int,
    workspace: FileJobWorkspace,
    for_index: bool = False,
) -> dict[str, RenameFileTarget]:
    if existing[source] and existing[destination]:
        raise CommandError(
            _("Cannot replay saved rename {old} -> {new}: both paths exist.").format(
                old=display_path(source),
                new=display_path(destination),
            )
        )
    current_path = source if existing[source] else destination
    if not existing[current_path]:
        raise CommandError(
            _(
                "Cannot replay saved rename {old} -> {new}: both paths are missing."
            ).format(
                old=display_path(source),
                new=display_path(destination),
            )
        )
    current_mode = modes[current_path]
    mode = saved_mode if current_mode == base_mode else current_mode
    buffer = _merge_contents(
        source,
        destination,
        current_path=artifacts[current_path],
        before_path=before,
        after_path=after,
        is_binary=is_binary,
        ordinal=ordinal,
        workspace=workspace,
    )
    if for_index and not is_binary:
        # Index publication writes blobs directly. Apply the destination's
        # clean conversion here, including when the merge kept current bytes.
        with buffer:
            blob = create_git_blob(buffer.byte_chunks(), path=destination)
        buffer = load_git_blob_as_buffer(blob, spool_dir=workspace.root)
    targets = {
        destination: RenameFileTarget(
            destination,
            buffer,
            mode,
            TextFileChangeType.ADDED,
        )
    }
    if existing[source]:
        targets[source] = RenameFileTarget(
            source, None, None, TextFileChangeType.DELETED
        )
    return targets


def _merge_contents(
    source: str,
    destination: str,
    *,
    current_path: Path,
    before_path: Path,
    after_path: Path,
    is_binary: bool,
    ordinal: int,
    workspace: FileJobWorkspace,
) -> LineBuffer:
    with (
        workspace.read_buffer(current_path) as current,
        workspace.read_buffer(before_path) as before,
        workspace.read_buffer(after_path) as after,
    ):
        line_ending = choose_line_ending(current, after) if not is_binary else None
        if is_binary:
            merged_path = workspace.write_buffer(ordinal, "rename-merged", current)
        else:
            # Git blobs and saved worktree snapshots can use different line
            # endings. Compare and merge text in one representation, retaining
            # the raw captures for stale-input checks and unchanged output.
            before_path = workspace.write_buffer(
                ordinal, "rename-before-lf", normalize_line_sequence_endings(before)
            )
            after_path = workspace.write_buffer(
                ordinal, "rename-after-lf", normalize_line_sequence_endings(after)
            )
            merged_path = workspace.write_buffer(
                ordinal, "rename-merged", normalize_line_sequence_endings(current)
            )
    if filecmp.cmp(before_path, after_path, shallow=False) or filecmp.cmp(
        merged_path, after_path, shallow=False
    ):
        return workspace.read_buffer(current_path)
    if filecmp.cmp(merged_path, before_path, shallow=False):
        merged_path = after_path
    else:
        merge = run_git_command(
            ["merge-file", str(merged_path), str(before_path), str(after_path)],
            check=False,
            requires_index_lock=False,
        )
        if merge.returncode != 0:
            raise CommandError(
                _(
                    "Cannot replay saved rename {old} -> {new}: content edits conflict."
                ).format(
                    old=display_path(source),
                    new=display_path(destination),
                )
            )
    with workspace.read_buffer(merged_path) as merged:
        return LineBuffer.from_chunks(
            restore_line_endings_in_chunks(merged.byte_chunks(), line_ending),
            spool_dir=workspace.root,
        )
