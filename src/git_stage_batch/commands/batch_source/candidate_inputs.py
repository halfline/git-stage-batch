"""Input metadata helpers for batch-source candidate operations."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

from ...batch.submodule_pointer import is_batch_submodule_pointer
from ...core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
)
from ...utils.git_repository import get_git_repository_root_path


@dataclass(frozen=True)
class CandidateBatchSourceRef:
    """Git object reference for one text candidate's batch source."""

    commit: str
    object_spec: str


@dataclass(frozen=True)
class CandidateWorktreeTarget:
    """Worktree metadata needed to build text candidate previews."""

    exists: bool
    file_mode: str | None
    text_change_type: TextFileChangeType


@dataclass(frozen=True)
class CandidateIndexTarget:
    """Index metadata needed to build text candidate previews."""

    exists: bool
    file_mode: str | None


def is_text_candidate_entry(file_meta: dict) -> bool:
    """Return whether a batch file entry supports text candidate handling."""
    return file_meta.get("file_type") != "binary" and not is_batch_submodule_pointer(
        file_meta
    )


def candidate_batch_source_ref(
    file_path: str,
    file_meta: dict,
) -> CandidateBatchSourceRef | None:
    """Return the batch source object reference, or None when metadata lacks one."""
    batch_source_commit = file_meta.get("batch_source_commit")
    if not batch_source_commit:
        return None
    return CandidateBatchSourceRef(
        commit=batch_source_commit,
        object_spec=f"{batch_source_commit}:{file_path}",
    )


def require_candidate_batch_source_ref(
    file_path: str,
    file_meta: dict,
) -> CandidateBatchSourceRef:
    """Return the batch source object reference for validated candidate metadata."""
    batch_source_commit = file_meta["batch_source_commit"]
    return CandidateBatchSourceRef(
        commit=batch_source_commit,
        object_spec=f"{batch_source_commit}:{file_path}",
    )


def candidate_worktree_text_target(
    *,
    file_path: str,
    file_meta: dict,
    selected_ids: Iterable[int] | None,
) -> CandidateWorktreeTarget:
    """Return worktree existence, materialization mode, and text lifecycle."""
    repo_root = get_git_repository_root_path()
    working_exists = os.path.lexists(repo_root / file_path)
    return CandidateWorktreeTarget(
        exists=working_exists,
        file_mode=mode_for_text_materialization(
            _batch_file_mode(file_meta),
            selected_ids,
            destination_exists=working_exists,
        ),
        text_change_type=normalized_text_change_type(file_meta.get("change_type")),
    )


def candidate_index_text_target(
    *,
    file_meta: dict,
    selected_ids: Iterable[int] | None,
    index_exists: bool,
) -> CandidateIndexTarget:
    """Return index existence and materialization mode metadata."""
    return CandidateIndexTarget(
        exists=index_exists,
        file_mode=mode_for_text_materialization(
            _batch_file_mode(file_meta),
            selected_ids,
            destination_exists=index_exists,
        ),
    )


def _batch_file_mode(file_meta: dict) -> str:
    return str(file_meta.get("mode", "100644"))
