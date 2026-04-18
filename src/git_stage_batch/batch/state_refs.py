"""Git-backed authoritative batch state refs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from .ref_names import BATCH_CONTENT_REF_PREFIX, BATCH_STATE_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from .validation import validate_batch_name
from ..utils.file_io import read_text_file_contents
from ..utils.git import create_git_blob, run_git_command, update_git_refs
from ..utils.paths import get_batch_metadata_file_path


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
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def sync_batch_state_refs(batch_name: str, *, content_commit: str | None = None) -> None:
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

    temp_index = tempfile.NamedTemporaryFile(delete=False, suffix=".index")
    temp_index_path = temp_index.name
    temp_index.close()
    os.unlink(temp_index_path)

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = temp_index_path

    try:
        for file_path, file_meta in metadata.get("files", {}).items():
            state_file_meta = dict(file_meta)

            source_commit = file_meta.get("batch_source_commit")
            if source_commit:
                source_result = run_git_command(
                    ["show", f"{source_commit}:{file_path}"],
                    check=False,
                    text_output=False,
                )
                if source_result.returncode == 0:
                    source_blob = create_git_blob([source_result.stdout])
                    source_path = f"sources/{file_path}"
                    subprocess.run(
                        [
                            "git",
                            "update-index",
                            "--add",
                            "--cacheinfo",
                            f"{file_meta.get('mode', '100644')},{source_blob},{source_path}",
                        ],
                        env=env,
                        capture_output=True,
                        check=True,
                    )
                    state_file_meta["source_path"] = source_path

            state_metadata["files"][file_path] = state_file_meta

        state_json = json.dumps(state_metadata, indent=2).encode("utf-8")
        state_blob = create_git_blob([state_json])
        subprocess.run(
            [
                "git",
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{state_blob},batch.json",
            ],
            env=env,
            capture_output=True,
            check=True,
        )

        tree_result = subprocess.run(
            ["git", "write-tree"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        tree_sha = tree_result.stdout.strip()

        existing_state = run_git_command(
            ["rev-parse", "--verify", get_batch_state_ref_name(batch_name)],
            check=False,
        )
        parent_args: list[str] = []
        if existing_state.returncode == 0 and existing_state.stdout.strip():
            parent_args = ["-p", existing_state.stdout.strip()]

        commit_result = subprocess.run(
            ["git", "commit-tree", tree_sha, *parent_args, "-m", f"Batch state: {batch_name}"],
            capture_output=True,
            text=True,
            check=True,
        )
        state_commit = commit_result.stdout.strip()

        update_git_refs(
            updates=[
                (get_batch_content_ref_name(batch_name), content_commit),
                (get_batch_state_ref_name(batch_name), state_commit),
            ],
            deletes=[get_legacy_batch_ref_name(batch_name)],
        )
        remove_file_backed_batch_metadata(batch_name)
    finally:
        if os.path.exists(temp_index_path):
            os.unlink(temp_index_path)
