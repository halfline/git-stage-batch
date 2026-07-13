"""Git worktree operation helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence

from .git_command import run_git_command


def git_apply_to_worktree(
    patch_chunks: Iterable[bytes],
    *,
    reverse: bool = False,
    unidiff_zero: bool = False,
    check_only: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Apply patch chunks to the working tree without writing the index."""
    arguments = ["apply", "--whitespace=nowarn"]
    if reverse:
        arguments.append("--reverse")
    if unidiff_zero:
        arguments.append("--unidiff-zero")
    if check_only:
        arguments.append("--check")
    return run_git_command(
        arguments,
        stdin_chunks=patch_chunks,
        check=check,
        requires_index_lock=False,
    )


def git_checkout_paths(
    treeish: str,
    paths: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Check out paths from a treeish into the index and working tree."""
    return run_git_command(
        ["checkout", treeish, "--", *paths],
        check=check,
        requires_index_lock=True,
    )


def git_checkout_index_paths(
    paths: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Restore working-tree paths from the index without changing the index."""
    return run_git_command(
        ["checkout", "--", *paths],
        check=check,
        requires_index_lock=True,
    )


def git_checkout_detached(
    oid: str,
    *,
    cwd: str,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Check out one commit in detached mode inside another Git worktree."""
    return run_git_command(
        ["checkout", "--detach", oid],
        cwd=cwd,
        check=check,
        requires_index_lock=True,
    )


def git_remove_paths(
    paths: Sequence[str],
    *,
    cached: bool = False,
    force: bool = False,
    quiet: bool = False,
    ignore_unmatch: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Remove paths from the index, and from the worktree unless cached."""
    arguments = ["rm"]
    if cached:
        arguments.append("--cached")
    if force:
        arguments.append("-f")
    if quiet:
        arguments.append("--quiet")
    if ignore_unmatch:
        arguments.append("--ignore-unmatch")
    arguments.extend(["--", *paths])
    return run_git_command(arguments, check=check, requires_index_lock=True)


def git_reset_hard(
    revision: str,
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Reset HEAD, index, and worktree to a revision."""
    return run_git_command(
        ["reset", "--hard", revision],
        env=env,
        check=check,
        requires_index_lock=True,
    )


def git_apply_stash(
    stash_ref: str,
    *,
    restore_index: bool = False,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Apply a stash to the worktree, optionally restoring index state."""
    arguments = ["stash", "apply"]
    if restore_index:
        arguments.append("--index")
    arguments.append(stash_ref)
    return run_git_command(
        arguments,
        env=env,
        check=check,
        requires_index_lock=True,
    )


def git_submodule_update_checkout(
    paths: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Ensure submodule worktrees exist using checkout update mode."""
    return run_git_command(
        ["submodule", "update", "--init", "--checkout", "--", *paths],
        check=check,
        requires_index_lock=True,
    )
