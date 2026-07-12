"""Batch query operations: list, read metadata, get refs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from .reference_names import BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from .references import (
    get_authoritative_batch_commit_sha,
    get_legacy_batch_ref_name,
    read_batch_state_metadata_for_batches,
    read_batch_state_metadata,
)
from .compatibility_metadata import read_file_backed_batch_metadata_model
from .batch_names import invalid_file_backed_batch_names, validate_batch_name
from ...exceptions import CommandError
from ...i18n import _
from ...utils.git_command import run_git_command
from ...git_paths import decode_path, nul_records


def read_batch_metadata(name: str) -> dict:
    """Read metadata for a batch.

    Returns metadata with structure:
    {
        "note": str,
        "created_at": str,
        "baseline": str | None,
        "files": {
            "path": {
                "batch_source_commit": str,  # Batch source SHA
                "presence_claims": list[dict],  # [{"source_lines": ["1-5"]}]
                "deletions": list[dict],  # [{"after_source_line": int|None, "blob": str}]
                "replacement_units": list[dict],  # optional presence/deletion coupling plus original-unit context
                "mode": str,  # File mode (e.g. "100644")
                "change_type": str,  # optional text lifecycle: "added" or "deleted"
            }
        }
    }
    """
    validate_batch_name(name)

    state_metadata = read_batch_state_metadata(name)
    if state_metadata is not None:
        return state_metadata

    return _read_file_backed_batch_metadata_or_empty(name)


def read_batch_metadata_for_batches(batch_names: Iterable[str]) -> dict[str, dict]:
    """Read metadata for many batches with one state-ref lookup pass."""
    unique_batch_names = list(dict.fromkeys(batch_names))
    for batch_name in unique_batch_names:
        validate_batch_name(batch_name)
    if not unique_batch_names:
        return {}

    metadata_by_name = read_batch_state_metadata_for_batches(unique_batch_names)
    missing_batch_names = [
        batch_name
        for batch_name in unique_batch_names
        if batch_name not in metadata_by_name
    ]
    for batch_name in missing_batch_names:
        metadata_by_name[batch_name] = _read_file_backed_batch_metadata_or_empty(
            batch_name
        )
    return metadata_by_name


def _empty_batch_metadata() -> dict:
    return {
        "note": "",
        "created_at": "",
        "baseline": None,
        "files": {},
    }


def _read_file_backed_batch_metadata_or_empty(name: str) -> dict:
    model = read_file_backed_batch_metadata_model(name)
    if model is None:
        return _empty_batch_metadata()
    return model.to_application_dict()


def get_batch_commit_sha(name: str) -> Optional[str]:
    """Get the commit SHA for a batch from its authoritative git ref."""
    validate_batch_name(name)

    commit_sha = get_authoritative_batch_commit_sha(name)
    if commit_sha:
        return commit_sha

    result = run_git_command(
        ["rev-parse", "--verify", get_legacy_batch_ref_name(name)],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None

    return result.stdout.strip()


def list_batch_names(*, validate_legacy_metadata: bool = True) -> list[str]:
    """List all batch names by querying authoritative refs and legacy imports."""
    invalid_legacy_names = (
        invalid_file_backed_batch_names()
        if validate_legacy_metadata
        else []
    )
    if invalid_legacy_names:
        formatted_names = ", ".join(repr(name) for name in invalid_legacy_names)
        raise CommandError(
            _(
                "Legacy batch metadata has invalid batch name(s): {names}. "
                "Move these entries out of the batch metadata directory or rename "
                "them and any corresponding refs/batches refs to valid, unused names."
            ).format(names=formatted_names)
        )

    batch_names: set[str] = set()
    for prefix in (BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX):
        result = run_git_command(["for-each-ref", "--format=%(refname)", prefix], check=False, requires_index_lock=False)
        if result.returncode != 0:
            continue
        for line in result.stdout.strip().splitlines():
            if line.startswith(prefix):
                batch_names.add(line[len(prefix):])

    return sorted(batch_names)


def get_batch_tree_sha(name: str) -> Optional[str]:
    """Get the tree SHA from a batch commit."""
    validate_batch_name(name)

    commit_sha = get_batch_commit_sha(name)
    if not commit_sha:
        return None

    # Get tree SHA from commit
    result = run_git_command(
        ["rev-parse", f"{commit_sha}^{{tree}}"],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None

    return result.stdout.strip()


def list_batch_files(name: str) -> list[str]:
    """List all files in a batch by reading its tree."""
    validate_batch_name(name)

    tree_sha = get_batch_tree_sha(name)
    if not tree_sha:
        return []

    # Use git ls-tree to list files recursively
    result = run_git_command(
        ["ls-tree", "-rz", "--name-only", tree_sha],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return []

    files = [decode_path(path) for path in nul_records(result.stdout)]
    return sorted(files)


def get_batch_baseline_commit(name: str) -> Optional[str]:
    """
    Get the baseline commit for a batch.

    Returns the HEAD commit that was selected when the batch was created.
    This is stored in the batch metadata.
    """
    validate_batch_name(name)

    metadata = read_batch_metadata(name)
    return metadata.get("baseline", None)
