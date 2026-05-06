"""Git command execution utilities."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
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


def stream_git_command(
    arguments: list[str],
    stdin_chunks: Iterable[bytes] | None = None,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
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

    Yields:
        Bytes lines from stdout

    Raises:
        subprocess.CalledProcessError: If git command fails (includes stderr)
    """
    def stdout_chunks():
        """Generator that yields only stdout chunks from command events."""
        nonlocal exit_code, stderr_chunks
        for event in stream_command(["git", *arguments], stdin_chunks, cwd=cwd, env=env):
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


def _git_ref_exists(ref_name: str) -> bool:
    result = run_git_command(["rev-parse", "--verify", ref_name], check=False)
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
    for _chunk in stream_git_command(["update-ref", "--stdin"], [payload]):
        pass


def run_git_command(
    arguments: list[str],
    check: bool = True,
    text_output: bool = True,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Execute a git command with error handling.

    Args:
        arguments: Git command arguments (e.g., ["status", "--short"])
        check: Whether to raise CalledProcessError on non-zero exit
        text_output: Whether to decode stdout/stderr as text
        cwd: Working directory for the command
        env: Environment variables

    Returns:
        CompletedProcess with returncode, stdout, stderr

    Raises:
        subprocess.CalledProcessError: If check=True and command fails
    """
    return run_command(
        ["git", *arguments],
        check=check,
        text_output=text_output,
        cwd=cwd,
        env=env,
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
    run_git_command(["read-tree", treeish], env=env)


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
    ):
        pass


def git_write_tree(*, env: dict[str, str] | None = None) -> str:
    """Write the current or provided index as a Git tree."""
    return run_git_command(["write-tree"], env=env).stdout.strip()


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
    return run_git_command(arguments, env=env).stdout.strip()


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
    stderr_chunks = []
    exit_code = 0

    for event in stream_command(["git", "hash-object", "-w", "--stdin"], content_chunks):
        if isinstance(event, ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, OutputEvent):
            if event.fd == 1:  # stdout
                stdout_chunks.append(event.data)
            elif event.fd == 2:  # stderr
                stderr_chunks.append(event.data)

    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise RuntimeError(f"git hash-object failed with exit code {exit_code}: {stderr_text}")

    if not stdout_chunks:
        raise RuntimeError("git hash-object produced no output")

    # git hash-object outputs a single line with the SHA
    stdout_bytes = b"".join(stdout_chunks)
    blob_sha = stdout_bytes.strip().decode("utf-8")
    return blob_sha


def create_git_blobs(contents: Iterable[bytes]) -> list[str]:
    """Create several blob objects with one hash-object process."""
    content_list = list(contents)
    if not content_list:
        return []

    with tempfile.TemporaryDirectory(prefix="git-stage-batch-blobs-") as temp_dir:
        temp_path = Path(temp_dir)
        path_lines: list[str] = []
        for index, content in enumerate(content_list):
            path = temp_path / str(index)
            path.write_bytes(content)
            path_lines.append(str(path))

        payload = ("\n".join(path_lines) + "\n").encode("utf-8")
        result = run_command(
            ["git", "hash-object", "-w", "--stdin-paths"],
            [payload],
        )

    blob_shas = result.stdout.strip().splitlines()
    if len(blob_shas) != len(content_list):
        raise RuntimeError(
            f"git hash-object returned {len(blob_shas)} hashes for {len(content_list)} blobs"
        )
    return blob_shas


def read_git_blob(blob_sha: str) -> Iterator[bytes]:
    """Read a git blob object as a stream.

    Args:
        blob_sha: SHA-1 hash of the blob to read

    Yields:
        Binary chunks from the blob content

    Raises:
        RuntimeError: If git cat-file fails or blob doesn't exist
    """
    stderr_chunks = []
    exit_code = 0

    for event in stream_command(["git", "cat-file", "blob", blob_sha], None):
        if isinstance(event, ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, OutputEvent):
            if event.fd == 1:  # stdout
                yield event.data
            elif event.fd == 2:  # stderr
                stderr_chunks.append(event.data)

    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise RuntimeError(f"git cat-file failed with exit code {exit_code}: {stderr_text}")


def read_git_blobs_as_bytes(blob_hashes: Iterable[str]) -> dict[str, bytes]:
    """Read multiple git blobs with one cat-file process."""
    unique_blob_hashes = list(dict.fromkeys(blob_hashes))
    if not unique_blob_hashes:
        return {}

    payload = "".join(f"{blob_hash}\n" for blob_hash in unique_blob_hashes).encode("ascii")
    result = run_command(
        ["git", "cat-file", "--batch"],
        [payload],
        text_output=False,
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


def read_git_tree_file_contents(treeish: str, file_paths: Iterable[str]) -> dict[str, bytes]:
    """Read several file contents from a tree with batched object IO."""
    tree_blobs = list_git_tree_blobs(treeish, file_paths)
    if not tree_blobs:
        return {}

    blob_contents = read_git_blobs_as_bytes(
        blob.blob_sha for blob in tree_blobs.values()
    )
    return {
        file_path: blob_contents[blob.blob_sha]
        for file_path, blob in tree_blobs.items()
        if blob.blob_sha in blob_contents
    }


def read_git_blob_as_bytes(blob_hash: str) -> bytes | None:
    """Read a git blob object as bytes.

    Args:
        blob_hash: SHA-1 hash of the blob to read

    Returns:
        Blob content as bytes, or None if blob doesn't exist or read fails
    """
    try:
        return b"".join(read_git_blob(blob_hash))
    except RuntimeError:
        return None


def read_git_object_as_lines(revision_path: str) -> list[bytes]:
    """Read a git object and split into lines.

    Args:
        revision_path: Git revision path (e.g., "HEAD:file.txt", "abc123:path/to/file")

    Returns:
        List of bytes lines, or empty list if object doesn't exist
    """
    result = run_git_command(["show", revision_path], check=False, text_output=False)
    if result.returncode != 0:
        return []
    return list(bytes_to_lines([result.stdout]))


def read_working_tree_file_as_lines(file_path: str) -> list[bytes]:
    """Read a working tree file and split into lines.

    Args:
        file_path: Repository-relative file path

    Returns:
        List of bytes lines, or empty list if file doesn't exist
    """
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    try:
        return list(bytes_to_lines([file_full_path.read_bytes()]))
    except Exception:
        return []


def require_git_repository() -> None:
    """Verify that we are inside a git repository.

    Calls exit_with_error if not in a git repository, printing git's
    error message for context.

    Raises:
        SystemExit: Via exit_with_error if not in a git repository
    """
    try:
        run_git_command(["rev-parse", "--git-dir"])
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

    output = run_git_command(["rev-parse", "--show-toplevel"]).stdout.strip()
    path = Path(output)
    _GIT_REPOSITORY_ROOT_CACHE[cwd] = path
    return path


def get_git_directory_path() -> Path:
    """Get the absolute path to the repository's git directory."""
    cwd = Path.cwd()
    cached = _GIT_DIRECTORY_CACHE.get(cwd)
    if cached is not None:
        return cached

    output = run_git_command(["rev-parse", "--absolute-git-dir"]).stdout.strip()
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
