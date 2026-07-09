"""Git command execution utilities."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..exceptions import exit_with_error
from ..i18n import _
from . import git_index_lock
from .command import ExitEvent, OutputEvent, run_command, stream_command
from .git_environment import git_environment_with_optional_locks_disabled
from .text import bytes_to_lines


_GIT_REPOSITORY_ROOT_CACHE: dict[Path, Path] = {}
_GIT_DIRECTORY_CACHE: dict[Path, Path] = {}


@dataclass(frozen=True)
class GitIndexEntryUpdate:
    """One index-info update for a temporary Git index."""

    file_path: str
    mode: str | None = None
    blob_sha: str | None = None
    force_remove: bool = False


def _prepare_git_command_environment(
    *,
    requires_index_lock: bool,
    cwd: str | None,
    env: dict[str, str] | None,
) -> dict[str, str] | None:
    if requires_index_lock:
        git_index_lock.wait_for_git_index_lock(cwd=cwd, env=env)
    return _git_command_environment(requires_index_lock=requires_index_lock, env=env)


def _git_command_environment(
    *,
    requires_index_lock: bool,
    env: dict[str, str] | None,
) -> dict[str, str] | None:
    if requires_index_lock:
        return env
    return git_environment_with_optional_locks_disabled(env)


def _index_lock_error_text(stream: str | bytes | None) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def _is_git_index_lock_error(result: subprocess.CompletedProcess) -> bool:
    stderr_text = _index_lock_error_text(result.stderr).lower()
    return "index.lock" in stderr_text and (
        "file exists" in stderr_text
        or "unable to create" in stderr_text
    )


def _raise_git_command_error(result: subprocess.CompletedProcess) -> None:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _remaining_index_lock_wait_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def stream_git_command(
    arguments: list[str],
    stdin_chunks: Iterable[bytes] | None = None,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    requires_index_lock: bool = True,
) -> Iterator[bytes]:
    """Stream git command output as bytes lines.

    Wrapper around stream_command that:
    1. Prepends "git" to arguments
    2. Streams stdout as bytes lines (split at \\n)
    3. Captures stderr for error reporting
    4. Raises on non-zero exit with stderr in exception

    Args:
        arguments: Git command arguments (e.g., ["diff", "--no-color"])
        stdin_chunks: Iterable yielding bytes chunks to write to stdin (None for no input)
        cwd: Working directory for the command
        env: Environment variables
        requires_index_lock: Whether to wait for Git's index lock before running

    Yields:
        Bytes lines from stdout

    Raises:
        subprocess.CalledProcessError: If git command fails (includes stderr)
    """
    def stdout_chunks():
        """Generator that yields only stdout chunks from command events."""
        nonlocal exit_code, stderr_chunks
        for event in stream_command(
            ["git", *arguments],
            stdin_chunks,
            cwd=cwd,
            env=_prepare_git_command_environment(
                requires_index_lock=requires_index_lock,
                cwd=cwd,
                env=env,
            ),
        ):
            if isinstance(event, ExitEvent):
                exit_code = event.exit_code
            elif isinstance(event, OutputEvent):
                if event.fd == 1:  # stdout
                    yield event.data
                elif event.fd == 2:  # stderr
                    stderr_chunks.append(event.data)

    exit_code = 0
    stderr_chunks = []

    # Convert binary stdout chunks to text lines
    yield from bytes_to_lines(stdout_chunks())

    # Check exit code after stream completes
    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise subprocess.CalledProcessError(
            exit_code,
            ["git", *arguments],
            stderr=stderr_text,
        )


def stream_git_diff(
    *,
    base: str | None = None,
    target: str | None = None,
    cached: bool = False,
    context_lines: int | None = None,
    no_color: bool = True,
    full_index: bool = False,
    find_renames: bool = False,
    no_renames: bool = False,
    ignore_submodules: str | None = None,
    submodule_format: str | None = None,
    paths: Iterable[str] = (),
    stdin_chunks: Iterable[bytes] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[bytes]:
    """Stream a Git diff using keyworded options."""
    config_arguments = []
    arguments = []
    if ignore_submodules is not None:
        config_arguments.extend(["-c", f"diff.ignoreSubmodules={ignore_submodules}"])
        arguments.append(f"--ignore-submodules={ignore_submodules}")
    if submodule_format is not None:
        config_arguments.extend(["-c", f"diff.submodule={submodule_format}"])
        arguments.append(f"--submodule={submodule_format}")
    if full_index:
        arguments.append("--full-index")
    if find_renames and not no_renames:
        arguments.append("--find-renames")
    if no_renames:
        arguments.append("--no-renames")
    if no_color:
        arguments.append("--no-color")
    if cached:
        arguments.append("--cached")
    if context_lines is not None:
        arguments.append(f"-U{context_lines}")
    if base is not None:
        arguments.append(base)
    if target is not None:
        arguments.append(target)

    path_list = list(paths)
    if path_list:
        arguments.extend(["--", *path_list])

    return stream_git_command(
        [
            *config_arguments,
            "diff",
            *arguments,
        ],
        stdin_chunks,
        cwd=cwd,
        env=env,
        requires_index_lock=False,
    )


def _git_ref_exists(ref_name: str) -> bool:
    result = run_git_command(
        ["rev-parse", "--verify", ref_name],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 0


def update_git_refs(
    *,
    updates: Iterable[tuple[str, str]] = (),
    deletes: Iterable[str] = (),
    ignore_missing_deletes: bool = True,
) -> None:
    """Update one or more Git refs in a single update-ref transaction."""
    update_commands = list(updates)
    delete_commands = list(deletes)
    if ignore_missing_deletes:
        delete_commands = [ref_name for ref_name in delete_commands if _git_ref_exists(ref_name)]
    if not update_commands and not delete_commands:
        return

    commands = ["start"]
    commands.extend(f"update {ref_name} {object_name}" for ref_name, object_name in update_commands)
    commands.extend(f"delete {ref_name}" for ref_name in delete_commands)
    commands.extend(["prepare", "commit"])
    payload = ("\n".join(commands) + "\n").encode("utf-8")
    for _chunk in stream_git_command(
        ["update-ref", "--stdin"],
        [payload],
        requires_index_lock=False,
    ):
        pass


def run_git_command(
    arguments: list[str],
    check: bool = True,
    text_output: bool = True,
    *,
    stdin_chunks: Iterable[bytes] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
    requires_index_lock: bool = True,
) -> subprocess.CompletedProcess:
    """Execute a git command with error handling.

    Args:
        arguments: Git command arguments (e.g., ["status", "--short"])
        check: Whether to raise CalledProcessError on non-zero exit
        text_output: Whether to decode stdout/stderr as text
        stdin_chunks: Iterable yielding bytes chunks to write to stdin
        cwd: Working directory for the command
        env: Environment variables
        capture_stdout: Whether to capture stdout
        capture_stderr: Whether to capture stderr
        requires_index_lock: Whether to wait for Git's index lock before running

    Returns:
        CompletedProcess with returncode, stdout, stderr

    Raises:
        subprocess.CalledProcessError: If check=True and command fails
    """
    command = ["git", *arguments]
    reusable_stdin_chunks = (
        list(stdin_chunks)
        if requires_index_lock and stdin_chunks is not None
        else stdin_chunks
    )
    retry_deadline = (
        time.monotonic() + git_index_lock.DEFAULT_INDEX_LOCK_WAIT_SECONDS
        if requires_index_lock
        else None
    )

    if retry_deadline is not None:
        git_index_lock.wait_for_git_index_lock(
            cwd=cwd,
            env=env,
            timeout_seconds=_remaining_index_lock_wait_seconds(retry_deadline),
        )

    while True:
        result = run_command(
            command,
            reusable_stdin_chunks,
            check=False,
            text_output=text_output,
            cwd=cwd,
            env=_git_command_environment(
                requires_index_lock=requires_index_lock,
                env=env,
            ),
            capture_stdout=capture_stdout,
            capture_stderr=capture_stderr,
        )
        if not (
            retry_deadline is not None
            and result.returncode != 0
            and _is_git_index_lock_error(result)
        ):
            break

        remaining_seconds = _remaining_index_lock_wait_seconds(retry_deadline)
        if remaining_seconds <= 0:
            break
        git_index_lock.wait_for_git_index_lock(
            cwd=cwd,
            env=env,
            timeout_seconds=remaining_seconds,
        )

    if check and result.returncode != 0:
        _raise_git_command_error(result)
    return result


@contextmanager
def temp_git_index() -> Iterator[dict[str, str]]:
    """Create a temporary Git index and yield an environment that uses it."""
    temp_index = tempfile.NamedTemporaryFile(delete=False, suffix=".index")
    temp_index_path = temp_index.name
    temp_index.close()
    os.unlink(temp_index_path)

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = temp_index_path
    try:
        yield env
    finally:
        if os.path.exists(temp_index_path):
            os.unlink(temp_index_path)


def git_read_tree(treeish: str, *, env: dict[str, str] | None = None) -> None:
    """Read a Git tree into the current or provided index."""
    run_git_command(["read-tree", treeish], env=env, requires_index_lock=True)


def git_update_index(
    *,
    file_path: str,
    mode: str | None = None,
    blob_sha: str | None = None,
    force_remove: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Update one index entry from a blob, or force-remove it."""
    if force_remove:
        if mode is not None or blob_sha is not None:
            raise ValueError("mode and blob_sha cannot be used with force_remove=True")
        arguments = ["update-index", "--force-remove", "--", file_path]
    else:
        if mode is None or blob_sha is None:
            raise ValueError("mode and blob_sha are required unless force_remove=True")
        arguments = ["update-index", "--add", "--cacheinfo", mode, blob_sha, file_path]

    return run_git_command(
        arguments,
        check=check,
        env=env,
        requires_index_lock=True,
    )


def git_refresh_index(*, check: bool = True) -> subprocess.CompletedProcess:
    """Refresh cached index stat information from the working tree."""
    return run_git_command(
        ["update-index", "--refresh"],
        check=check,
        requires_index_lock=True,
    )


def git_update_gitlink(
    *,
    file_path: str,
    oid: str | None,
    remove: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Update one index entry that stores a submodule commit pointer."""
    if remove:
        if oid is not None:
            raise ValueError("oid cannot be used with remove=True")
        return git_update_index(
            file_path=file_path,
            force_remove=True,
            check=check,
            env=env,
        )

    if oid is None:
        raise ValueError("oid is required unless remove=True")

    return git_update_index(
        file_path=file_path,
        mode="160000",
        blob_sha=oid,
        check=check,
        env=env,
    )


def git_update_index_entries(
    entries: Iterable[GitIndexEntryUpdate],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Update several index entries through one update-index process."""
    payload_chunks: list[bytes] = []
    for entry in entries:
        path_bytes = entry.file_path.encode("utf-8")
        if entry.force_remove:
            if entry.mode is not None or entry.blob_sha is not None:
                raise ValueError("mode and blob_sha cannot be used with force_remove=True")
            payload_chunks.extend([
                b"0 0000000000000000000000000000000000000000\t",
                path_bytes,
                b"\0",
            ])
        else:
            if entry.mode is None or entry.blob_sha is None:
                raise ValueError("mode and blob_sha are required unless force_remove=True")
            payload_chunks.extend([
                entry.mode.encode("ascii"),
                b" ",
                entry.blob_sha.encode("ascii"),
                b"\t",
                path_bytes,
                b"\0",
            ])

    if not payload_chunks:
        return

    for _chunk in stream_git_command(
        ["update-index", "-z", "--index-info"],
        payload_chunks,
        env=env,
        requires_index_lock=True,
    ):
        pass


def git_write_tree(*, env: dict[str, str] | None = None) -> str:
    """Write the current or provided index as a Git tree."""
    return run_git_command(
        ["write-tree"],
        env=env,
        requires_index_lock=False,
    ).stdout.strip()


def git_commit_tree(
    tree_sha: str,
    *,
    parents: Iterable[str] = (),
    message: str,
    env: dict[str, str] | None = None,
) -> str:
    """Create a commit object from a tree and optional parents."""
    arguments = ["commit-tree", tree_sha]
    for parent in parents:
        arguments.extend(["-p", parent])
    arguments.extend(["-m", message])
    return run_git_command(arguments, env=env, requires_index_lock=False).stdout.strip()


def git_apply_to_index(
    patch_chunks: Iterable[bytes],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Apply patch chunks to the index."""
    return run_git_command(
        ["apply", "--cached", "--whitespace=nowarn"],
        stdin_chunks=patch_chunks,
        check=check,
        requires_index_lock=True,
    )


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


def git_add_paths(
    paths: Sequence[str],
    *,
    intent_to_add: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Stage paths, optionally as intent-to-add entries."""
    arguments = ["add"]
    if intent_to_add:
        arguments.append("-N")
    arguments.extend(["--", *paths])
    return run_git_command(arguments, check=check, requires_index_lock=True)


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


def git_reset_paths(
    paths: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Reset paths in the index from HEAD."""
    return run_git_command(
        ["reset", "--", *paths],
        check=check,
        requires_index_lock=True,
    )


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


def require_git_repository() -> None:
    """Verify that we are inside a git repository.

    Calls exit_with_error if not in a git repository, printing git's
    error message for context.

    Raises:
        SystemExit: Via exit_with_error if not in a git repository
    """
    try:
        run_git_command(["rev-parse", "--git-dir"], requires_index_lock=False)
    except subprocess.CalledProcessError as error:
        # Print git's actual error message which contains helpful context
        if error.stderr:
            print(error.stderr.rstrip(), file=sys.stderr)
        exit_with_error(_("Not inside a git repository."), exit_code=error.returncode)


def get_git_repository_root_path() -> Path:
    """Get the absolute path to the git repository root.

    Returns:
        Path object pointing to the repository root directory

    Raises:
        subprocess.CalledProcessError: If not in a git repository
    """
    cwd = Path.cwd()
    cached = _GIT_REPOSITORY_ROOT_CACHE.get(cwd)
    if cached is not None:
        return cached

    output = run_git_command(
        ["rev-parse", "--show-toplevel"],
        requires_index_lock=False,
    ).stdout.strip()
    path = Path(output)
    _GIT_REPOSITORY_ROOT_CACHE[cwd] = path
    return path


def get_git_directory_path() -> Path:
    """Get the absolute path to the repository's git directory."""
    cwd = Path.cwd()
    cached = _GIT_DIRECTORY_CACHE.get(cwd)
    if cached is not None:
        return cached

    output = run_git_command(
        ["rev-parse", "--absolute-git-dir"],
        requires_index_lock=False,
    ).stdout.strip()
    path = Path(output)
    _GIT_DIRECTORY_CACHE[cwd] = path
    return path


def resolve_file_path_to_repo_relative(file_path: str) -> str:
    """Convert a file path to repository-relative format.

    Args:
        file_path: File path to convert

    Returns:
        Repository-relative path, or original path if outside repo
    """
    repo_root = get_git_repository_root_path()
    path = Path(file_path)

    # If it's already relative, use it as-is
    if not path.is_absolute():
        return file_path

    # If it's absolute, make it relative to repo root
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        # Path is outside repo, return as-is
        return file_path
