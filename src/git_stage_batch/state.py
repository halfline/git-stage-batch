"""State management and filesystem utilities for git-stage-batch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .i18n import _


class CommandError(Exception):
    """Raised when a command fails and needs to exit with an error code."""

    def __init__(self, message: str, exit_code: int = 1):
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


# --------------------------- Utility: git and filesystem ---------------------------

def run_git_command(arguments: list[str],
                    check: bool = True,
                    text_output: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *arguments], check=check, text=text_output, capture_output=True)

def stream_git_command(arguments: list[str]) -> Iterator[str]:
    """Stream git command output line-by-line.

    If the caller stops consuming early, the git process is terminated
    and no error is raised for that intentional cancellation.

    Args:
        arguments: Git command arguments (e.g., ["diff", "--no-color"])

    Yields:
        Lines from git's stdout

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    process = subprocess.Popen(
        ["git", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    cancelled = False

    assert process.stdout is not None
    assert process.stderr is not None

    try:
        for line in process.stdout:
            yield line
    except GeneratorExit:
        cancelled = True

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        process.stdout.close()

        if process.poll() is None:
            process.wait()

        if not cancelled and process.returncode != 0:
            stderr = process.stderr.read()
            raise subprocess.CalledProcessError(
                process.returncode,
                ["git", *arguments],
                stderr=stderr,
            )

        process.stderr.close()

def require_git_repository() -> None:
    try:
        run_git_command(["rev-parse", "--git-dir"])
    except subprocess.CalledProcessError as error:
        # Print git's actual error message which contains helpful context
        if error.stderr:
            print(error.stderr.rstrip(), file=sys.stderr)
        exit_with_error(_("Not inside a git repository."), exit_code=error.returncode)

def get_git_repository_root_path() -> Path:
    output = run_git_command(["rev-parse", "--show-toplevel"]).stdout.strip()
    return Path(output)

def exit_with_error(message: str, exit_code: int = 1) -> None:
    """Raise a CommandError instead of exiting directly."""
    raise CommandError(message, exit_code)


def read_text_file_contents(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape") if path.exists() else ""


def write_text_file_contents(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", errors="surrogateescape")


def append_lines_to_file(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="surrogateescape") as file_handle:
        for line in lines:
            file_handle.write(str(line).rstrip() + "\n")


def read_file_paths_file(path: Path) -> list[str]:
    """Read a file containing one path per line, returning a deduplicated sorted list."""
    content = read_text_file_contents(path)
    if not content:
        return []
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return sorted(set(lines))


def write_file_paths_file(path: Path, file_paths: Iterable[str]) -> None:
    """Write file paths to a file, one per line, sorted and deduplicated."""
    unique_paths = sorted(set(file_paths))
    content = "\n".join(unique_paths)
    if unique_paths:
        content += "\n"
    write_text_file_contents(path, content)


def append_file_path_to_file(path: Path, file_path: str) -> None:
    """Append a file path to a list file, ensuring no duplicates."""
    existing = read_file_paths_file(path)
    if file_path not in existing:
        existing.append(file_path)
        write_file_paths_file(path, existing)


def remove_file_path_from_file(state_file_path: Path, file_path: str) -> None:
    """Remove a file path from a list file."""
    existing = read_file_paths_file(state_file_path)
    if file_path in existing:
        existing.remove(file_path)
        write_file_paths_file(state_file_path, existing)


def resolve_file_path_to_repo_relative(file_path: str) -> str:
    """Convert a file path to repository-relative format."""
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


# --------------------------- State paths ---------------------------

def get_state_directory_path() -> Path:
    return get_git_repository_root_path() / ".git" / "git-stage-batch"


def ensure_state_directory_exists() -> None:
    get_state_directory_path().mkdir(parents=True, exist_ok=True)


def get_context_lines_file_path() -> Path:
    return get_state_directory_path() / "context-lines"


def get_context_lines() -> int:
    """Get stored context lines value, defaulting to 3."""
    context_file = get_context_lines_file_path()
    if context_file.exists():
        try:
            return int(read_text_file_contents(context_file).strip())
        except ValueError:
            return 3
    return 3


def get_block_list_file_path() -> Path:
    return get_state_directory_path() / "blocklist"


def get_current_hunk_patch_file_path() -> Path:
    return get_state_directory_path() / "current-hunk-patch"


def get_current_hunk_hash_file_path() -> Path:
    return get_state_directory_path() / "current-hunk-hash"


def get_abort_head_file_path() -> Path:
    return get_state_directory_path() / "abort-head"


def get_abort_stash_file_path() -> Path:
    return get_state_directory_path() / "abort-stash"


def get_abort_snapshots_directory_path() -> Path:
    return get_state_directory_path() / "snapshots"


def get_abort_snapshot_list_file_path() -> Path:
    return get_state_directory_path() / "snapshot-list"


def get_auto_added_files_file_path() -> Path:
    return get_state_directory_path() / "auto-added-files"


def get_blocked_files_file_path() -> Path:
    return get_state_directory_path() / "blocked-files"


def get_processed_include_ids_file_path() -> Path:
    return get_state_directory_path() / "processed.include"


def get_processed_skip_ids_file_path() -> Path:
    return get_state_directory_path() / "processed.skip"


def get_current_lines_json_file_path() -> Path:
    return get_state_directory_path() / "current-lines.json"


def get_index_snapshot_file_path() -> Path:
    return get_state_directory_path() / "index-snapshot"


def get_working_tree_snapshot_file_path() -> Path:
    return get_state_directory_path() / "working-tree-snapshot"


def get_suggest_fixup_state_file_path() -> Path:
    return get_state_directory_path() / "suggest-fixup-state.json"


def get_iteration_count_file_path() -> Path:
    return get_state_directory_path() / "iteration-count"


def get_included_hunks_file_path() -> Path:
    return get_state_directory_path() / "included-hunks"


def get_skipped_hunks_jsonl_file_path() -> Path:
    return get_state_directory_path() / "skipped-hunks.jsonl"


def get_discarded_hunks_file_path() -> Path:
    return get_state_directory_path() / "discarded-hunks"


def get_gitignore_path() -> Path:
    return get_git_repository_root_path() / ".gitignore"


# --------------------------- Diff streaming helpers ---------------------------

def get_next_hunk_from_git(
    context_lines: int,
    predicate: Optional[Callable[[str], bool]] = None
) -> Optional['SingleHunkPatch']:
    """Stream git diff and find the first hunk matching the predicate.

    Args:
        context_lines: Number of context lines for diff (-U parameter)
        predicate: Optional function that takes patch text and returns True if
                   the hunk should be returned. If None, returns first hunk.

    Returns:
        SingleHunkPatch if a matching hunk is found, None otherwise
    """
    from .parser import parse_unified_diff_streaming
    from .models import SingleHunkPatch

    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{context_lines}", "--no-color"])):
        if predicate is None:
            return patch

        patch_text = patch.to_patch_text()
        if predicate(patch_text):
            return patch

    return None


def get_next_file_from_git(
    context_lines: int,
    predicate: Optional[Callable[[str], bool]] = None
) -> Optional[str]:
    """Stream git diff and find the first file with a hunk matching the predicate.

    Args:
        context_lines: Number of context lines for diff (-U parameter)
        predicate: Optional function that takes patch text and returns True if
                   the hunk counts as a match. If None, returns first file.

    Returns:
        File path if a matching file is found, None otherwise
    """
    from .parser import parse_unified_diff_streaming

    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{context_lines}", "--no-color"])):
        if predicate is None:
            return patch.new_path

        patch_text = patch.to_patch_text()
        if predicate(patch_text):
            return patch.new_path

    return None


# --------------------------- .gitignore manipulation ---------------------------


def read_gitignore_lines() -> list[str]:
    """Read .gitignore file, returning lines preserving original formatting."""
    gitignore_path = get_gitignore_path()
    if not gitignore_path.exists():
        return []
    content = read_text_file_contents(gitignore_path)
    # Preserve exact formatting including trailing newline
    return content.splitlines(keepends=True)


def write_gitignore_lines(lines: list[str]) -> None:
    """Write lines to .gitignore, preserving formatting."""
    gitignore_path = get_gitignore_path()
    content = "".join(lines)
    write_text_file_contents(gitignore_path, content)


def add_file_to_gitignore(file_path: str) -> None:
    """Add a file path to .gitignore."""
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
    """Remove a file path from .gitignore. Returns True if removed."""
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
