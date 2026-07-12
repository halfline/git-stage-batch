"""Persistence helpers for sifted batch transform results."""

from __future__ import annotations

from ...batch.state.lifecycle import create_batch, delete_batch
from ...batch.state.compatibility_metadata import write_file_backed_batch_metadata
from ...batch.ownership.model import BatchOwnership
from ...batch.state.query import get_batch_baseline_commit, read_batch_metadata
from ...batch.state.references import (
    delete_batch_state_refs,
    get_batch_content_ref_name,
    sync_batch_state_refs,
)
from ...batch.state.content_commits import (
    remove_file_from_batch_commit,
    update_batch_commit,
)
from ...batch.binary_file_storage import add_binary_file_to_batch
from ...batch.file_mode_storage import add_file_mode_to_batch
from ...batch.state.batch_names import batch_exists, validate_batch_name
from ...core.buffer import LineBuffer
from ...core.text_lifecycle import TextFileChangeType, normalized_text_change_type
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git_command import run_git_command
from ...utils.git_index import (
    git_commit_tree,
    git_read_tree,
    git_update_index,
    git_write_tree,
    temp_git_index,
)
from ...utils.git_object_io import create_git_blob
from .sift_results import SiftedBinaryFileResult, SiftedFileResult, SiftedModeFileResult


RetainedSiftedFile = tuple[str, dict, SiftedFileResult]


def create_synthetic_batch_source_commit(
    baseline_commit: str,
    file_path: str,
    file_buffer: LineBuffer,
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
        git_update_index(
            mode=file_mode,
            blob_sha=blob_sha,
            file_path=file_path,
            env=env,
        )
        new_tree = git_write_tree(env=env)

    return git_commit_tree(
        new_tree,
        parents=[baseline_commit],
        message=f"Sift batch source for {file_path}",
    )


def add_sifted_text_file_to_batch(
    batch_name: str,
    file_path: str,
    target_buffer: LineBuffer,
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
        exit_with_error(
            _("Batch '{name}' has no baseline commit").format(name=batch_name)
        )

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

    write_file_backed_batch_metadata(batch_name, metadata)

    source_buffers = {file_path: target_buffer}
    if text_change_type == TextFileChangeType.DELETED:
        remove_file_from_batch_commit(
            batch_name,
            file_path,
            source_buffers=source_buffers,
        )
    else:
        update_batch_commit(
            batch_name,
            file_path,
            target_blob_sha,
            file_mode,
            source_buffers=source_buffers,
        )


def add_sifted_file_to_batch(
    batch_name: str,
    file_path: str,
    file_meta: dict,
    result: SiftedFileResult,
) -> None:
    """Persist any retained sifted file result into a batch."""
    file_mode = file_meta.get("mode", "100644")

    if isinstance(result, SiftedBinaryFileResult):
        add_binary_file_to_batch(
            batch_name,
            result.binary_change,
            file_mode=file_mode,
            file_buffer_override=result.target_buffer,
        )
        return

    if isinstance(result, SiftedModeFileResult):
        add_file_mode_to_batch(batch_name, result.mode_change)
        return

    add_sifted_text_file_to_batch(
        batch_name=batch_name,
        file_path=file_path,
        target_buffer=result.target_buffer,
        ownership=result.ownership,
        file_mode=file_mode,
        change_type=result.change_type,
    )


def replace_batch_with_sifted_files(
    batch_name: str,
    retained_files: list[RetainedSiftedFile],
    source_metadata: dict,
) -> None:
    """Replace a batch atomically with retained sifted file results."""
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
            add_sifted_file_to_batch(
                temp_batch_name,
                file_path,
                file_meta,
                result,
            )

        temp_commit = run_git_command(
            ["rev-parse", get_batch_content_ref_name(temp_batch_name)],
            check=False,
            requires_index_lock=False,
        )
        if temp_commit.returncode == 0:
            commit_sha = temp_commit.stdout.strip()
            temp_metadata = read_batch_metadata(temp_batch_name)
            temp_metadata["revision"] = source_metadata.get("revision")
            write_file_backed_batch_metadata(batch_name, temp_metadata)
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
    retained_files: list[RetainedSiftedFile],
) -> dict[str, LineBuffer]:
    source_buffers: dict[str, LineBuffer] = {}
    for file_path, _file_meta, result in retained_files:
        target_buffer = result.target_source_buffer()
        if target_buffer is not None:
            source_buffers[file_path] = target_buffer
    return source_buffers
