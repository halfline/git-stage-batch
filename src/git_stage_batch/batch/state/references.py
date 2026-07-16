"""Git-backed authoritative batch state refs."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .reference_names import (
    format_batch_content_ref_name,
    format_batch_state_ref_name,
    format_legacy_batch_ref_name,
)
from .compatibility_metadata import read_file_backed_batch_metadata_model
from .metadata_schema import (
    BatchMetadata,
    decode_batch_metadata,
    encode_batch_metadata,
    metadata_from_application_dict,
)
from .batch_names import validate_batch_name
from ...exceptions import BatchMetadataError
from ...core.buffer import LineBuffer
from ...utils.repository_buffers import read_git_object_buffer_or_none
from ...utils.git_command import (
    run_git_command,
)
from ...utils.git_refs import (
    update_git_refs,
)
from ...utils.git_index import (
    GitIndexEntryUpdate,
    git_commit_tree,
    git_update_index_entries,
    git_write_tree,
    temp_git_index,
)
from ...utils.git_object_io import create_git_blob, read_git_blobs_as_bytes
from ...utils.paths import get_batch_metadata_file_path


_StateBufferData = bytes | LineBuffer


@dataclass(frozen=True)
class _StateBufferUpdate:
    path: str
    data: _StateBufferData
    mode: str = "100644"


def _buffer_chunks(buffer: _StateBufferData):
    if isinstance(buffer, LineBuffer):
        yield from buffer.byte_chunks()
    else:
        yield buffer


def get_batch_content_ref_name(batch_name: str) -> str:
    """Return the authoritative content ref for a batch."""
    validate_batch_name(batch_name)
    return format_batch_content_ref_name(batch_name)


def get_batch_state_ref_name(batch_name: str) -> str:
    """Return the authoritative state ref for a batch."""
    validate_batch_name(batch_name)
    return format_batch_state_ref_name(batch_name)


def get_legacy_batch_ref_name(batch_name: str) -> str:
    """Return the compatibility content ref for a batch."""
    validate_batch_name(batch_name)
    return format_legacy_batch_ref_name(batch_name)


def delete_batch_state_refs(batch_name: str) -> None:
    """Delete authoritative batch state/content refs for a batch."""
    validate_batch_name(batch_name)
    update_git_refs(deletes=[
        get_batch_content_ref_name(batch_name),
        get_batch_state_ref_name(batch_name),
        get_legacy_batch_ref_name(batch_name),
    ])


def remove_file_backed_batch_metadata(batch_name: str) -> None:
    """Remove legacy file-backed metadata for a migrated batch."""
    metadata_dir = get_batch_metadata_file_path(batch_name).parent
    if metadata_dir.exists():
        shutil.rmtree(metadata_dir, ignore_errors=True)


def _legacy_batch_commit_sha(batch_name: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", get_legacy_batch_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def read_file_backed_batch_metadata(batch_name: str) -> dict:
    model = read_file_backed_batch_metadata_model(batch_name)
    if model is None:
        return {
            "note": "",
            "created_at": "",
            "baseline": None,
            "files": {},
        }
    return model.to_application_dict()


def _decode_state_metadata(payload: str | bytes, batch_name: str) -> BatchMetadata:
    return decode_batch_metadata(payload, expected_batch=batch_name)


def read_batch_state_metadata(batch_name: str) -> dict[str, Any] | None:
    """Read normalized batch metadata from the authoritative state ref."""
    validate_batch_name(batch_name)
    result = run_git_command(
        ["show", f"{get_batch_state_ref_name(batch_name)}:batch.json"],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None

    return _decode_state_metadata(result.stdout, batch_name).to_application_dict()


def read_batch_state_metadata_for_batches(
    batch_names: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Read normalized state-ref metadata for many batches in one Git process."""
    unique_batch_names = list(dict.fromkeys(batch_names))
    for batch_name in unique_batch_names:
        validate_batch_name(batch_name)
    if not unique_batch_names:
        return {}

    refspec_by_name = {
        batch_name: f"{get_batch_state_ref_name(batch_name)}:batch.json"
        for batch_name in unique_batch_names
    }
    blobs = read_git_blobs_as_bytes(refspec_by_name.values())

    metadata_by_name: dict[str, dict[str, Any]] = {}
    for batch_name, refspec in refspec_by_name.items():
        blob = blobs.get(refspec)
        if blob is None:
            continue
        metadata_by_name[batch_name] = _decode_state_metadata(
            blob,
            batch_name,
        ).to_application_dict()
    return metadata_by_name


def get_authoritative_batch_commit_sha(batch_name: str) -> str | None:
    """Get the batch content commit from the authoritative content ref."""
    validate_batch_name(batch_name)
    result = run_git_command(
        ["rev-parse", "--verify", get_batch_content_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def sync_batch_state_refs(
    batch_name: str,
    *,
    content_commit: str | None = None,
    source_buffers: dict[str, LineBuffer] | None = None,
) -> None:
    """Publish batch metadata and source snapshots into authoritative Git refs."""
    validate_batch_name(batch_name)

    existing_content_commit = get_authoritative_batch_commit_sha(batch_name)
    content_commit = content_commit or existing_content_commit or _legacy_batch_commit_sha(batch_name)
    if not content_commit:
        delete_batch_state_refs(batch_name)
        return

    file_backed_model = read_file_backed_batch_metadata_model(batch_name)
    if file_backed_model is None:
        raise ValueError(f"Batch '{batch_name}' has no file-backed metadata to publish")
    metadata = file_backed_model.to_application_dict()

    existing_state_ref = run_git_command(
        ["rev-parse", "--verify", get_batch_state_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    existing_state_commit = (
        existing_state_ref.stdout.strip()
        if existing_state_ref.returncode == 0
        else None
    )
    if existing_state_commit is not None:
        existing_state = run_git_command(
            ["show", f"{existing_state_commit}:batch.json"],
            requires_index_lock=False,
        )
        existing_state_model = _decode_state_metadata(existing_state.stdout, batch_name)
        if file_backed_model.revision != existing_state_model.revision:
            remove_file_backed_batch_metadata(batch_name)
            raise BatchMetadataError(
                f"Batch '{batch_name}' changed after its metadata was read. "
                "Retry the operation against the latest batch state."
            )

    state_files = {}

    source_buffers = source_buffers or {}
    buffer_updates: list[_StateBufferUpdate] = []
    managed_buffers: list[LineBuffer] = []

    try:
        with temp_git_index() as env:
            for file_path, file_meta in metadata.get("files", {}).items():
                state_file_meta = dict(file_meta)

                source_commit = file_meta.get("batch_source_commit")
                if source_commit:
                    source_buffer = source_buffers.get(file_path)
                    if source_buffer is None:
                        source_buffer = read_git_object_buffer_or_none(
                            f"{source_commit}:{file_path}"
                        )
                        if source_buffer is not None:
                            managed_buffers.append(source_buffer)

                    if source_buffer is not None:
                        source_path = f"sources/{file_path}"
                        buffer_updates.append(
                            _StateBufferUpdate(
                                path=source_path,
                                data=source_buffer,
                                mode=file_meta.get("mode", "100644"),
                            )
                        )
                        state_file_meta["source_path"] = source_path

                state_files[file_path] = state_file_meta

            state_model = metadata_from_application_dict(
                batch_name,
                {**metadata, "files": state_files},
                content_ref=get_batch_content_ref_name(batch_name),
                content_commit=content_commit,
                new_revision=True,
            )
            state_json = encode_batch_metadata(state_model).encode("utf-8")
            buffer_updates.append(_StateBufferUpdate(path="batch.json", data=state_json))
            blob_shas = [
                create_git_blob(_buffer_chunks(update.data))
                for update in buffer_updates
            ]
            git_update_index_entries(
                [
                    GitIndexEntryUpdate(
                        file_path=update.path,
                        mode=update.mode,
                        blob_sha=blob_sha,
                    )
                    for update, blob_sha in zip(buffer_updates, blob_shas, strict=True)
                ],
                env=env,
            )

            tree_sha = git_write_tree(env=env)
    finally:
        for buffer in managed_buffers:
            buffer.close()

    parents = [existing_state_commit] if existing_state_commit is not None else []

    state_commit = git_commit_tree(
        tree_sha,
        parents=parents,
        message=f"Batch state: {batch_name}",
    )

    try:
        update_git_refs(
            updates=[
                (get_batch_content_ref_name(batch_name), content_commit),
                (get_batch_state_ref_name(batch_name), state_commit),
            ],
            deletes=[get_legacy_batch_ref_name(batch_name)],
            expected_old_values={
                get_batch_content_ref_name(batch_name): existing_content_commit,
                get_batch_state_ref_name(batch_name): existing_state_commit,
            },
        )
    except subprocess.CalledProcessError as error:
        remove_file_backed_batch_metadata(batch_name)
        raise BatchMetadataError(
            f"Batch '{batch_name}' changed while its metadata was being published. "
            "Retry the operation against the latest batch state."
        ) from error
    remove_file_backed_batch_metadata(batch_name)
