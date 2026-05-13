"""Git-backed authoritative batch state refs."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from typing import Any

from .ref_names import BATCH_CONTENT_REF_PREFIX, BATCH_STATE_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from .validation import validate_batch_name
from ..editor import EditorBuffer, load_git_object_as_buffer
from ..utils.file_io import read_text_file_contents
from ..utils.git import (
    create_git_blob,
    GitIndexEntryUpdate,
    git_commit_tree,
    git_update_index_entries,
    git_write_tree,
    run_git_command,
    temp_git_index,
    update_git_refs,
)
from ..utils.paths import get_batch_metadata_file_path


_StateBufferData = bytes | EditorBuffer


@dataclass(frozen=True)
class _StateBufferUpdate:
    path: str
    data: _StateBufferData
    mode: str = "100644"


def _buffer_chunks(buffer: _StateBufferData):
    if isinstance(buffer, EditorBuffer):
        yield from buffer.byte_chunks()
    else:
        yield buffer


def get_batch_content_ref_name(batch_name: str) -> str:
    """Return the authoritative content ref for a batch."""
    validate_batch_name(batch_name)
    return f"{BATCH_CONTENT_REF_PREFIX}{batch_name}"


def get_batch_state_ref_name(batch_name: str) -> str:
    """Return the authoritative state ref for a batch."""
    validate_batch_name(batch_name)
    return f"{BATCH_STATE_REF_PREFIX}{batch_name}"


def get_legacy_batch_ref_name(batch_name: str) -> str:
    """Return the compatibility content ref for a batch."""
    validate_batch_name(batch_name)
    return f"{LEGACY_BATCH_REF_PREFIX}{batch_name}"


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


def read_file_backed_batch_metadata(batch_name: str) -> dict[str, Any]:
    metadata_path = get_batch_metadata_file_path(batch_name)
    if not metadata_path.exists():
        return {
            "note": "",
            "created_at": "",
            "baseline": None,
            "files": {},
        }

    metadata = json.loads(read_text_file_contents(metadata_path))
    return {
        "note": metadata.get("note", ""),
        "created_at": metadata.get("created_at", ""),
        "baseline": metadata.get("baseline", None),
        "files": metadata.get("files", {}),
    }


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

    state_metadata = json.loads(result.stdout)
    return {
        "note": state_metadata.get("note", ""),
        "created_at": state_metadata.get("created_at", ""),
        "baseline": state_metadata.get("baseline_commit", state_metadata.get("baseline")),
        "files": state_metadata.get("files", {}),
    }


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
    source_buffers: dict[str, EditorBuffer] | None = None,
) -> None:
    """Publish batch metadata and source snapshots into authoritative Git refs."""
    validate_batch_name(batch_name)

    content_commit = content_commit or get_authoritative_batch_commit_sha(batch_name) or _legacy_batch_commit_sha(batch_name)
    if not content_commit:
        delete_batch_state_refs(batch_name)
        return

    metadata = read_file_backed_batch_metadata(batch_name)
    state_metadata = {
        "batch": batch_name,
        "note": metadata.get("note", ""),
        "created_at": metadata.get("created_at", ""),
        "baseline_commit": metadata.get("baseline"),
        "content_ref": get_batch_content_ref_name(batch_name),
        "content_commit": content_commit,
        "files": {},
    }

    source_buffers = source_buffers or {}
    buffer_updates: list[_StateBufferUpdate] = []
    managed_buffers: list[EditorBuffer] = []

    try:
        with temp_git_index() as env:
            for file_path, file_meta in metadata.get("files", {}).items():
                state_file_meta = dict(file_meta)

                source_commit = file_meta.get("batch_source_commit")
                if source_commit:
                    source_buffer = source_buffers.get(file_path)
                    if source_buffer is None:
                        source_buffer = load_git_object_as_buffer(
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

                state_metadata["files"][file_path] = state_file_meta

            state_json = json.dumps(state_metadata, indent=2).encode("utf-8")
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

    existing_state = run_git_command(
        ["rev-parse", "--verify", get_batch_state_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    parents = []
    if existing_state.returncode == 0 and existing_state.stdout.strip():
        parents.append(existing_state.stdout.strip())

    state_commit = git_commit_tree(
        tree_sha,
        parents=parents,
        message=f"Batch state: {batch_name}",
    )

    update_git_refs(
        updates=[
            (get_batch_content_ref_name(batch_name), content_commit),
            (get_batch_state_ref_name(batch_name), state_commit),
        ],
        deletes=[get_legacy_batch_ref_name(batch_name)],
    )
    remove_file_backed_batch_metadata(batch_name)
