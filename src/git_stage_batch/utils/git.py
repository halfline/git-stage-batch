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
from .command import ExitEvent, OutputEvent, run_command, stream_command
from .file_io import read_text_file_contents, write_text_file_contents
from .text import bytes_to_lines


_GIT_REPOSITORY_ROOT_CACHE: dict[Path, Path] = {}
_GIT_DIRECTORY_CACHE: dict[Path, Path] = {}
_INDEX_LOCK_WAIT_SECONDS = 2.0
_INDEX_LOCK_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class GitTreeBlob:
    """One blob entry from a Git tree."""

    file_path: str
    mode: str
    blob_sha: str


@dataclass(frozen=True)
class GitIndexEntryUpdate:
    """One index-info update for a temporary Git index."""

    file_path: str
    mode: str | None = None
    blob_sha: str | None = None
    force_remove: bool = False


def _git_environment_with_optional_locks_disabled(
    env: dict[str, str] | None,
) -> dict[str, str]:
    git_env = os.environ.copy() if env is None else dict(env)
    git_env["GIT_OPTIONAL_LOCKS"] = "0"
    return git_env


def _custom_index_lock_path(
    *,
    env: dict[str, str] | None,
    cwd: str | None,
) -> Path | None:
    git_env = os.environ.copy() if env is None else dict(env)
    index_file = git_env.get("GIT_INDEX_FILE")
    if not index_file:
        return None

    index_path = Path(index_file)
    if not index_path.is_absolute():
        index_path = (Path.cwd() if cwd is None else Path(cwd)) / index_path
    return Path(f"{index_path}.lock")


def _git_index_lock_path(*, cwd: str | None, env: dict[str, str] | None) -> Path:
    custom_index_lock_path = _custom_index_lock_path(env=env, cwd=cwd)
    if custom_index_lock_path is not None:
        return custom_index_lock_path

    result = run_command(
        ["git", "rev-parse", "--absolute-git-dir"],
        check=True,
        cwd=cwd,
        env=_git_environment_with_optional_locks_disabled(env),
    )
    return Path(result.stdout.strip()) / "index.lock"


def wait_for_git_index_lock(
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = _INDEX_LOCK_WAIT_SECONDS,
    poll_seconds: float = _INDEX_LOCK_POLL_SECONDS,
) -> None:
    """Wait briefly for a pre-existing Git index lock to disappear."""
    try:
        index_lock_path = _git_index_lock_path(cwd=cwd, env=env)
    except subprocess.CalledProcessError:
        return

    deadline = time.monotonic() + timeout_seconds
    while index_lock_path.exists():
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return
        time.sleep(min(poll_seconds, remaining_seconds))


def _prepare_git_command_environment(
    *,
    requires_index_lock: bool,
    cwd: str | None,
    env: dict[str, str] | None,
) -> dict[str, str] | None:
    if requires_index_lock:
        wait_for_git_index_lock(cwd=cwd, env=env)
        return env
    return _git_environment_with_optional_locks_disabled(env)


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
    return run_command(
        ["git", *arguments],
        stdin_chunks,
        check=check,
        text_output=text_output,
        cwd=cwd,
        env=_prepare_git_command_environment(
            requires_index_lock=requires_index_lock,
            cwd=cwd,
            env=env,
        ),
        capture_stdout=capture_stdout,
        capture_stderr=capture_stderr,
    )


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
        ["apply", "--cached"],
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
    arguments = ["apply"]
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


def create_git_blob(content_chunks: Iterable[bytes]) -> str:
    """Create a git blob object from streaming content.

    Args:
        content_chunks: Iterable yielding binary content chunks to store

    Returns:
        SHA-1 hash of the created blob object

    Raises:
        RuntimeError: If git hash-object fails or produces no output
    """
    stdout_chunks = []
    try:
        for line in stream_git_command(
            ["hash-object", "-w", "--stdin"],
            content_chunks,
            requires_index_lock=False,
        ):
            stdout_chunks.append(line)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"git hash-object failed with exit code {error.returncode}: "
            f"{error.stderr}"
        ) from error

    if not stdout_chunks:
        raise RuntimeError("git hash-object produced no output")

    # git hash-object outputs a single line with the SHA
    stdout_bytes = b"".join(stdout_chunks)
    blob_sha = stdout_bytes.strip().decode("utf-8")
    return blob_sha


def read_git_blob(blob_sha: str) -> Iterator[bytes]:
    """Read a git blob object as a stream.

    Args:
        blob_sha: SHA-1 hash of the blob to read

    Yields:
        Binary chunks from the blob content

    Raises:
        RuntimeError: If git cat-file fails or blob doesn't exist
    """
    try:
        yield from stream_git_command(
            ["cat-file", "blob", blob_sha],
            requires_index_lock=False,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"git cat-file failed with exit code {error.returncode}: {error.stderr}"
        ) from error


def read_git_blobs_as_bytes(blob_hashes: Iterable[str]) -> dict[str, bytes]:
    """Read multiple git blobs with one cat-file process."""
    unique_blob_hashes = list(dict.fromkeys(blob_hashes))
    if not unique_blob_hashes:
        return {}

    payload = "".join(f"{blob_hash}\n" for blob_hash in unique_blob_hashes).encode("ascii")
    result = run_git_command(
        ["cat-file", "--batch"],
        stdin_chunks=[payload],
        text_output=False,
        requires_index_lock=False,
    )

    data = result.stdout
    blobs: dict[str, bytes] = {}
    offset = 0
    for requested_hash in unique_blob_hashes:
        header_end = data.index(b"\n", offset)
        header = data[offset:header_end].decode("ascii", errors="replace")
        offset = header_end + 1
        parts = header.split()
        if len(parts) >= 2 and parts[1] == "missing":
            continue
        if len(parts) < 3 or parts[1] != "blob":
            raise RuntimeError(f"Unexpected git cat-file --batch header: {header}")

        object_hash = parts[0]
        size = int(parts[2])
        content = data[offset:offset + size]
        offset += size
        if offset < len(data) and data[offset:offset + 1] == b"\n":
            offset += 1
        blobs[requested_hash] = content
        blobs[object_hash] = content

    return blobs


def list_git_tree_blobs(treeish: str, file_paths: Iterable[str]) -> dict[str, GitTreeBlob]:
    """List blob entries for paths in one tree with one ls-tree process."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    if not unique_file_paths:
        return {}

    result = run_git_command(
        ["ls-tree", "-rz", treeish, "--", *unique_file_paths],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return {}

    entries: dict[str, GitTreeBlob] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata_bytes, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        metadata = metadata_bytes.decode("ascii", errors="replace").split()
        if len(metadata) < 3 or metadata[1] != "blob":
            continue
        file_path = path_bytes.decode("utf-8")
        entries[file_path] = GitTreeBlob(
            file_path=file_path,
            mode=metadata[0],
            blob_sha=metadata[2],
        )
    return entries


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


def get_gitignore_path() -> Path:
    """Get the path to the repository's .gitignore file.

    Returns:
        Path to .gitignore
    """
    return get_git_repository_root_path() / ".gitignore"


def read_gitignore_lines() -> list[str]:
    """Read .gitignore file, returning lines preserving original formatting.

    Returns:
        List of lines from .gitignore with original formatting
    """
    gitignore_path = get_gitignore_path()
    if not gitignore_path.exists():
        return []
    content = read_text_file_contents(gitignore_path)
    # Preserve exact formatting including trailing newline
    return content.splitlines(keepends=True)


def write_gitignore_lines(lines: list[str]) -> None:
    """Write lines to .gitignore, preserving formatting.

    Args:
        lines: Lines to write to .gitignore
    """
    gitignore_path = get_gitignore_path()
    content = "".join(lines)
    write_text_file_contents(gitignore_path, content)


def add_file_to_gitignore(file_path: str) -> None:
    """Add a file path to .gitignore.

    Args:
        file_path: File path to add
    """
    lines = read_gitignore_lines()

    # Check if already present
    file_path_normalized = file_path.rstrip("\n")
    for line in lines:
        if line.rstrip("\n") == file_path_normalized:
            return  # Already present

    # Add to end
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    lines.append(f"{file_path}\n")

    write_gitignore_lines(lines)


def remove_file_from_gitignore(file_path: str) -> bool:
    """Remove a file path from .gitignore.

    Args:
        file_path: File path to remove

    Returns:
        True if removed, False if not found
    """
    lines = read_gitignore_lines()
    file_path_normalized = file_path.rstrip("\n")

    i = 0
    removed = False
    while i < len(lines):
        if lines[i].rstrip("\n") == file_path_normalized:
            # Remove the path
            del lines[i]
            removed = True
            continue  # Don't increment i, check same position again
        i += 1

    if removed:
        write_gitignore_lines(lines)

    return removed
