"""Batch metadata sanity checking and validation.

This module provides targeted hardening to detect missing or corrupted batch
metadata early and produce clear error messages.
"""

from __future__ import annotations

from typing import Any

from .query import read_batch_metadata
from .state_refs import get_batch_state_ref_name, get_legacy_batch_ref_name
from ..exceptions import BatchMetadataError
from ..i18n import _
from ..utils.git import run_git_command
from ..utils.paths import (
    get_batch_metadata_file_path,
    get_state_directory_path,
)


def validate_state_directory_exists() -> None:
    """Verify that the batch system state directory exists.

    Raises:
        BatchMetadataError: If .git/git-stage-batch is missing or inaccessible
    """
    state_dir = get_state_directory_path()
    if not state_dir.exists():
        raise BatchMetadataError(
            _("The git-stage-batch metadata directory is missing.\n"
              "Expected directory: {dir}\n"
              "This may indicate the batch session was not initialized properly.").format(
                dir=str(state_dir)
            )
        )

    if not state_dir.is_dir():
        raise BatchMetadataError(
            _("The git-stage-batch metadata path exists but is not a directory: {dir}").format(
                dir=str(state_dir)
            )
        )


def validate_batch_metadata_file_exists(batch_name: str) -> None:
    """Verify that compatibility metadata exists when only legacy refs exist.

    Args:
        batch_name: Name of the batch

    Raises:
        BatchMetadataError: If metadata file is missing for an existing batch
    """
    state_result = run_git_command(
        ["show-ref", "--verify", "--quiet", get_batch_state_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    if state_result.returncode == 0:
        return

    result = run_git_command(
        ["show-ref", "--verify", "--quiet", get_legacy_batch_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    batch_ref_exists = result.returncode == 0

    metadata_path = get_batch_metadata_file_path(batch_name)
    metadata_exists = metadata_path.exists()

    # Detect metadata/ref mismatch
    if batch_ref_exists and not metadata_exists:
        raise BatchMetadataError(
            _("Batch metadata is missing or corrupted for '{name}'.\n"
              "The legacy batch ref exists (refs/batches/{name}) but metadata file is missing.\n"
              "Expected metadata file: {file}\n"
              "The batch may not be recoverable automatically.").format(
                name=batch_name,
                file=str(metadata_path)
            )
        )

    if not batch_ref_exists and metadata_exists:
        # Metadata without a ref is ignored; the batch effectively does not exist.
        pass


def validate_batch_metadata_structure(metadata: dict[str, Any], batch_name: str) -> None:
    """Verify that batch metadata has required structure and fields.

    Args:
        metadata: Parsed batch metadata dictionary
        batch_name: Name of the batch (for error messages)

    Raises:
        BatchMetadataError: If metadata structure is invalid or missing required fields
    """
    # Handle empty metadata (can occur after abort or other edge cases)
    # Treat as valid but empty batch rather than corruption
    if not metadata or metadata == {}:
        return

    # Check for required top-level fields
    if "baseline" not in metadata:
        raise BatchMetadataError(
            _("Batch metadata for '{name}' is missing required field: 'baseline'.\n"
              "The metadata file may be corrupted.").format(name=batch_name)
        )

    baseline = metadata.get("baseline")
    if baseline is None:
        raise BatchMetadataError(
            _("Batch '{name}' has no baseline commit.\n"
              "The batch metadata exists but baseline is null or missing.\n"
              "This indicates corrupted or incomplete batch state.").format(name=batch_name)
        )

    # Validate baseline commit is a valid git object
    if baseline:
        result = run_git_command(
            ["cat-file", "-e", baseline],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0:
            raise BatchMetadataError(
                _("Batch '{name}' has invalid baseline commit: {baseline}\n"
                  "The baseline commit does not exist in the repository.\n"
                  "The batch metadata may be corrupted.").format(
                    name=batch_name,
                    baseline=baseline
                )
            )

    # Validate files structure if present
    if "files" in metadata:
        files = metadata["files"]
        if not isinstance(files, dict):
            raise BatchMetadataError(
                _("Batch metadata for '{name}' has invalid 'files' field (expected object).\n"
                  "The metadata file may be corrupted.").format(name=batch_name)
            )

        # Validate each file entry has required fields
        for file_path, file_meta in files.items():
            if not isinstance(file_meta, dict):
                raise BatchMetadataError(
                    _("Batch metadata for '{name}' has invalid file entry for '{file}'.\n"
                      "The metadata file may be corrupted.").format(
                        name=batch_name,
                        file=file_path
                    )
                )

            # Check for batch_source_commit (required for text files)
            if "batch_source_commit" not in file_meta:
                # Atomic files may not have batch_source_commit because they
                # use whole-entry storage rather than line ownership.
                if file_meta.get("file_type") not in {"binary", "gitlink"}:
                    raise BatchMetadataError(
                        _("Batch metadata for '{name}' is missing 'batch_source_commit' for file '{file}'.\n"
                          "The metadata file may be corrupted.").format(
                            name=batch_name,
                            file=file_path
                        )
                    )

            # Validate batch source commit exists
            batch_source_commit = file_meta.get("batch_source_commit")
            if batch_source_commit:
                result = run_git_command(
                    ["cat-file", "-e", batch_source_commit],
                    check=False,
                    requires_index_lock=False,
                )
                if result.returncode != 0:
                    raise BatchMetadataError(
                        _("Batch '{name}' has invalid batch_source_commit for file '{file}': {commit}\n"
                          "The batch source commit does not exist in the repository.\n"
                          "The batch metadata may be corrupted.").format(
                            name=batch_name,
                            file=file_path,
                            commit=batch_source_commit
                        )
                    )


def load_and_validate_batch_metadata(batch_name: str) -> dict[str, Any]:
    """Load batch metadata and validate its structure.

    This is the primary entry point for commands that need batch metadata.
    It validates the metadata structure and returns clean metadata.

    Args:
        batch_name: Name of the batch

    Returns:
        Validated batch metadata dictionary

    Raises:
        BatchMetadataError: If metadata is missing, corrupted, or structurally invalid
    """
    state_result = run_git_command(
        ["show-ref", "--verify", "--quiet", get_batch_state_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    state_ref_exists = state_result.returncode == 0

    legacy_result = run_git_command(
        ["show-ref", "--verify", "--quiet", get_legacy_batch_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    batch_ref_exists = state_ref_exists or legacy_result.returncode == 0

    if not batch_ref_exists:
        return {
            "note": "",
            "created_at": "",
            "baseline": None,
            "files": {}
        }

    if batch_ref_exists and not state_ref_exists:
        # Ensure metadata file exists for this batch
        validate_batch_metadata_file_exists(batch_name)

    try:
        metadata = read_batch_metadata(batch_name)
    except Exception as e:
        raise BatchMetadataError(
            _("Failed to read batch metadata for '{name}'.\n"
              "Error: {error}").format(
                name=batch_name,
                error=str(e)
            )
        )

    # Validate structure
    validate_batch_metadata_structure(metadata, batch_name)

    # Normalize and return
    return {
        "note": metadata.get("note", ""),
        "created_at": metadata.get("created_at", ""),
        "baseline": metadata.get("baseline", None),
        "files": metadata.get("files", {})
    }


def require_batch_metadata_sane(batch_name: str) -> None:
    """Require that batch metadata exists and is structurally valid.

    This is a lighter-weight check for commands that don't need the full metadata
    but want to fail early with a clear error if metadata is corrupted.

    Args:
        batch_name: Name of the batch

    Raises:
        BatchMetadataError: If metadata is missing or corrupted
    """
    load_and_validate_batch_metadata(batch_name)


def get_validated_baseline_commit(batch_name: str) -> str:
    """Get baseline commit for a batch with validation.

    This is a helper for commands that require a valid baseline commit.
    It validates metadata structure and ensures baseline is present.

    Args:
        batch_name: Name of the batch

    Returns:
        Baseline commit SHA (guaranteed non-None)

    Raises:
        BatchMetadataError: If metadata is missing, corrupted, or baseline is None
    """
    metadata = load_and_validate_batch_metadata(batch_name)
    baseline = metadata.get("baseline")

    if not baseline:
        raise BatchMetadataError(
            _("Batch '{name}' has no baseline commit.\n"
              "The batch metadata exists but baseline is null or missing.\n"
              "This indicates corrupted or incomplete batch state.").format(name=batch_name)
        )

    return baseline


def read_validated_batch_metadata(batch_name: str) -> dict[str, Any]:
    """Read and validate batch metadata (command entry point helper).

    This is a drop-in replacement for read_batch_metadata that adds validation.
    Commands should use this to get clear errors early if metadata is corrupted.

    Args:
        batch_name: Name of the batch

    Returns:
        Validated batch metadata dictionary

    Raises:
        BatchMetadataError: If metadata is missing, corrupted, or structurally invalid
    """
    return load_and_validate_batch_metadata(batch_name)
