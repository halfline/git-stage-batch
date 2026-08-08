"""Tree-replay commutation analysis for staged fixup units."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable

from ..core.buffer import LineBuffer
from ..utils.git_command import (
    run_git_command,
    stream_git_command,
    stream_git_command_bytes,
)
from ..utils.git_index import git_read_tree, git_write_tree, temp_git_index
from .models import FixupRange, FixupUnit, PlacementEvidence


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


def load_tree_diff_as_buffer(old_tree: str, new_tree: str) -> LineBuffer:
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
        requires_index_lock=False,
    )
    return LineBuffer.from_chunks(chunks)


def apply_patch_to_tree(
    base_tree: str,
    patch_chunks: Iterable[bytes],
    *,
    three_way: bool,
    unidiff_zero: bool = False,
) -> str | None:
    """Apply a patch to an isolated index and return its tree, or None."""
    arguments = ["apply", "--cached", "--whitespace=nowarn"]
    if three_way:
        arguments.append("--3way")
    if unidiff_zero:
        arguments.append("--unidiff-zero")

    with temp_git_index() as env:
        git_read_tree(base_tree, env=env)
        try:
            for _output_line in stream_git_command(
                arguments,
                patch_chunks,
                env=env,
                requires_index_lock=True,
            ):
                pass
        except subprocess.CalledProcessError:
            return None
        try:
            return git_write_tree(env=env)
        except subprocess.CalledProcessError:
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

    try:
        current_original_tree = tree_for_commit(commit_range.head_commit)
        current_modified_tree = apply_patch_to_tree(
            current_original_tree,
            unit.patch_buffer.byte_chunks(),
            three_way=False,
            unidiff_zero=True,
        )
        if current_modified_tree is None:
            return PlacementEvidence(
                status="unknown",
                barrier=None,
                commuted_across=(),
                detail="staged-unit-no-longer-applies-to-head",
            )

        commuted_across: list[str] = []
        for commit in commit_range.commits_newest_first:
            commit_tree = tree_for_commit(commit)
            if commit_tree != current_original_tree:
                return PlacementEvidence(
                    status="unknown",
                    barrier=None,
                    commuted_across=tuple(commuted_across),
                    detail="range-tree-chain-changed",
                )

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
                return PlacementEvidence(
                    status="barrier",
                    barrier=commit,
                    commuted_across=tuple(commuted_across),
                )

            replayed_tree: str | None
            if parent_tree == current_original_tree:
                # Empty commits replay to the already-relocated tree. Passing
                # an empty diff to `git apply` would incorrectly report a
                # blocker because Git rejects input containing no patch.
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
                return PlacementEvidence(
                    status="barrier",
                    barrier=commit,
                    commuted_across=tuple(commuted_across),
                )

            commuted_across.append(commit)
            current_original_tree = parent_tree
            current_modified_tree = earlier_modified_tree

        return PlacementEvidence(
            status="commutes-through",
            barrier=None,
            commuted_across=tuple(commuted_across),
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return PlacementEvidence(
            status="unknown",
            barrier=None,
            commuted_across=(),
            detail=type(error).__name__,
        )
