"""Content identities for live binary-file changes."""

from __future__ import annotations

from dataclasses import replace
import hashlib

from ..core.models import BinaryFileChange
from ..utils.git_command import run_git_command


def _object_name(specification: str) -> str:
    result = run_git_command(
        ["rev-parse", "--verify", specification],
        check=False,
        requires_index_lock=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "missing"


def _worktree_object_name(file_path: str) -> str:
    result = run_git_command(
        ["hash-object", "--no-filters", "--", file_path],
        check=False,
        requires_index_lock=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "missing"


def attach_live_binary_fingerprint(
    binary_change: BinaryFileChange,
    *,
    comparison_base: str | None = None,
) -> BinaryFileChange:
    """Attach a baseline/worktree content fingerprint to a live binary change."""
    file_path = binary_change.path()
    baseline_specification = (
        f"{comparison_base}:{binary_change.old_path}"
        if comparison_base is not None
        else f":{binary_change.old_path}"
    )
    parts = (
        _object_name(baseline_specification),
        _worktree_object_name(file_path),
    )
    fingerprint = hashlib.sha256("\0".join(parts).encode("ascii")).hexdigest()
    return replace(binary_change, content_fingerprint=fingerprint)
