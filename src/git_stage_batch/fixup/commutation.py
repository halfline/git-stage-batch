"""Tree-replay commutation analysis for staged fixup units."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from ..core.buffer import LineBuffer
from ..core.diff_parser import patch_requires_unidiff_zero
from ..utils.git_command import (
    run_git_command,
    stream_git_command,
    stream_git_command_bytes,
)
from ..utils.git_index import git_read_tree, git_write_tree, temp_git_index
from .models import FixupRange, FixupUnit, PlacementEvidence


class _RangeTreeChainChanged(ValueError):
    """Signal that a supposedly linear range no longer has matching trees."""


PatchApplicationStatus = Literal["APPLIED", "BLOCKED", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class PatchApplicationResult:
    """Tri-state result from applying an exact patch to an isolated tree."""

    status: PatchApplicationStatus
    tree: str | None
    detail: str | None = None


def tree_for_commit(commit: str) -> str:
    """Return the canonical tree object for a commit."""
    return run_git_command(
        ["rev-parse", "--verify", f"{commit}^{{tree}}"],
        requires_index_lock=False,
    ).stdout.strip()


def _parent_commit(commit: str) -> str:
    result = run_git_command(
        ["rev-list", "--parents", "-n", "1", commit],
        requires_index_lock=False,
    )
    fields = result.stdout.split()
    if len(fields) != 2:
        raise ValueError(f"commit {commit} does not have exactly one parent")
    return fields[1]


def load_tree_diff_as_buffer(
    old_tree: str,
    new_tree: str,
    *,
    env: dict[str, str] | None = None,
) -> LineBuffer:
    """Stream a full-index binary patch into bounded buffer storage."""
    chunks = stream_git_command_bytes(
        [
            "diff",
            "--binary",
            "--full-index",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--find-renames",
            old_tree,
            new_tree,
            "--",
        ],
        env=env,
        requires_index_lock=False,
    )
    return LineBuffer.from_chunks(chunks)


def apply_patch_to_tree(
    base_tree: str,
    patch_chunks: Iterable[bytes],
    *,
    three_way: bool,
    unidiff_zero: bool = False,
    env: dict[str, str] | None = None,
) -> str | None:
    """Apply a patch to an isolated index and return its tree, or None."""
    return apply_patch_to_tree_result(
        base_tree,
        patch_chunks,
        three_way=three_way,
        unidiff_zero=unidiff_zero,
        env=env,
    ).tree


def apply_patch_to_tree_result(
    base_tree: str,
    patch_chunks: Iterable[bytes],
    *,
    three_way: bool,
    unidiff_zero: bool = False,
    env: dict[str, str] | None = None,
) -> PatchApplicationResult:
    """Apply a patch with BLOCKED separated from operational uncertainty."""
    arguments = ["apply", "--cached", "--whitespace=nowarn"]
    if three_way:
        arguments.append("--3way")
    if unidiff_zero:
        arguments.append("--unidiff-zero")

    try:
        index_context = temp_git_index(base_env=env)
        with index_context as index_env:
            try:
                git_read_tree(base_tree, env=index_env)
            except (OSError, subprocess.CalledProcessError) as error:
                return PatchApplicationResult(
                    status="UNKNOWN",
                    tree=None,
                    detail=f"git-read-tree-{type(error).__name__}",
                )
            try:
                for _output_line in stream_git_command(
                    arguments,
                    patch_chunks,
                    env=index_env,
                    requires_index_lock=True,
                ):
                    pass
            except subprocess.CalledProcessError as error:
                return PatchApplicationResult(
                    status="BLOCKED" if error.returncode == 1 else "UNKNOWN",
                    tree=None,
                    detail=f"git-apply-exit-{error.returncode}",
                )
            try:
                tree = git_write_tree(env=index_env)
            except (OSError, subprocess.CalledProcessError) as error:
                return PatchApplicationResult(
                    status="UNKNOWN",
                    tree=None,
                    detail=f"git-write-tree-{type(error).__name__}",
                )
    except OSError as error:
        return PatchApplicationResult(
            status="UNKNOWN",
            tree=None,
            detail=f"temporary-index-{type(error).__name__}",
        )
    return PatchApplicationResult(status="APPLIED", tree=tree)


def _commute_across_commit(
    current_original_tree: str,
    current_modified_tree: str,
    commit: str,
) -> tuple[str, str] | None:
    commit_tree = tree_for_commit(commit)
    if commit_tree != current_original_tree:
        raise _RangeTreeChainChanged

    parent = _parent_commit(commit)
    parent_tree = tree_for_commit(parent)
    with load_tree_diff_as_buffer(
        current_original_tree,
        current_modified_tree,
    ) as relocation_patch:
        earlier_modified_tree = apply_patch_to_tree(
            parent_tree,
            relocation_patch.byte_chunks(),
            three_way=True,
        )
    if earlier_modified_tree is None:
        return None

    replayed_tree: str | None
    if parent_tree == current_original_tree:
        # Empty commits replay to the already-relocated tree. Passing an empty
        # diff to `git apply` would incorrectly report a blocker.
        replayed_tree = earlier_modified_tree
    else:
        with load_tree_diff_as_buffer(
            parent_tree,
            current_original_tree,
        ) as commit_patch:
            replayed_tree = apply_patch_to_tree(
                earlier_modified_tree,
                commit_patch.byte_chunks(),
                three_way=True,
            )
    if replayed_tree != current_modified_tree:
        return None
    return parent_tree, earlier_modified_tree


def analyze_patch_placement(
    patch_buffer: LineBuffer,
    commit_range: FixupRange,
) -> PlacementEvidence:
    """Find the first commit an exact textual patch cannot commute across."""
    commuted_across: list[str] = []
    try:
        current_original_tree = tree_for_commit(commit_range.head_commit)
        current_modified_tree = apply_patch_to_tree(
            current_original_tree,
            patch_buffer.byte_chunks(),
            three_way=False,
            unidiff_zero=patch_requires_unidiff_zero(patch_buffer),
        )
        if current_modified_tree is None:
            return PlacementEvidence(
                status="unknown",
                barrier=None,
                commuted_across=(),
                detail="staged-unit-no-longer-applies-to-head",
            )

        for commit in commit_range.commits_newest_first:
            crossing = _commute_across_commit(
                current_original_tree,
                current_modified_tree,
                commit,
            )
            if crossing is None:
                return PlacementEvidence(
                    status="barrier",
                    barrier=commit,
                    commuted_across=tuple(commuted_across),
                )
            current_original_tree, current_modified_tree = crossing
            commuted_across.append(commit)

        return PlacementEvidence(
            status="commutes-through",
            barrier=None,
            commuted_across=tuple(commuted_across),
        )
    except _RangeTreeChainChanged:
        return PlacementEvidence(
            status="unknown",
            barrier=None,
            commuted_across=tuple(commuted_across),
            detail="range-tree-chain-changed",
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return PlacementEvidence(
            status="unknown",
            barrier=None,
            commuted_across=tuple(commuted_across),
            detail=type(error).__name__,
        )


def relocate_patch_to_target(
    patch_buffer: LineBuffer,
    commit_range: FixupRange,
    target: str,
) -> str | None:
    """Return the target tree with a patch relocated after it, if proven."""
    if target not in commit_range.commits_newest_first:
        return None
    try:
        current_original_tree = tree_for_commit(commit_range.head_commit)
        current_modified_tree = apply_patch_to_tree(
            current_original_tree,
            patch_buffer.byte_chunks(),
            three_way=False,
            unidiff_zero=patch_requires_unidiff_zero(patch_buffer),
        )
        if current_modified_tree is None:
            return None

        for commit in commit_range.commits_newest_first:
            if commit == target:
                return current_modified_tree
            crossing = _commute_across_commit(
                current_original_tree,
                current_modified_tree,
                commit,
            )
            if crossing is None:
                return None
            current_original_tree, current_modified_tree = crossing
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    return None


def analyze_placement(
    unit: FixupUnit,
    commit_range: FixupRange,
) -> PlacementEvidence:
    """Find the first commit the fixup unit cannot commute backward past."""
    if unit.patch_buffer is None:
        return PlacementEvidence(
            status="unknown",
            barrier=None,
            commuted_across=(),
            detail="unit-has-no-text-patch",
        )
    return analyze_patch_placement(unit.patch_buffer, commit_range)
