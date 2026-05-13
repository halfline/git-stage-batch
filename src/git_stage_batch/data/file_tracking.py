"""Auto-add untracked files management."""

from __future__ import annotations

from collections.abc import Iterable

from ..utils.file_io import append_file_path_to_file, read_file_paths_file
from ..utils.git import get_git_repository_root_path, run_git_command
from ..utils.journal import log_journal
from ..utils.paths import get_auto_added_files_file_path


def _is_embedded_git_repository(file_path: str) -> bool:
    path = get_git_repository_root_path() / file_path.rstrip("/")
    return path.is_dir() and (path / ".git").exists()


def list_untracked_files(paths: Iterable[str] | None = None) -> list[str]:
    """Return untracked, non-ignored files, optionally limited to paths."""
    arguments = ["ls-files", "--others", "--exclude-standard"]
    if paths is not None:
        unique_paths = list(dict.fromkeys(paths))
        if not unique_paths:
            return []
        arguments.extend(["--", *unique_paths])

    result = run_git_command(arguments, check=False)
    if result.returncode != 0:
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def auto_add_untracked_files(paths: Iterable[str] | None = None) -> None:
    """Automatically run git add -N on untracked files (except blocked ones).

    This makes untracked files visible to git diff without staging their
    content, enabling the interactive staging workflow for new files.
    Files matching .gitignore patterns are automatically excluded.
    """
    untracked_files = list_untracked_files(paths)
    if not untracked_files:
        return

    untracked_files = list(dict.fromkeys(untracked_files))
    # Get already auto-added files
    auto_added_path = get_auto_added_files_file_path()
    auto_added_files = set(read_file_paths_file(auto_added_path))

    # Add untracked files even when they were recorded earlier. A user may have
    # removed the intent-to-add entry with git restore --staged during a session.
    for file_path in untracked_files:
        if _is_embedded_git_repository(file_path):
            log_journal("skip_auto_add_embedded_git_repository", file_path=file_path)
            continue

        # Get before state
        ls_before = run_git_command(["ls-files", "--stage", "--", file_path], check=False).stdout.strip()

        result = run_git_command(["add", "-N", "--", file_path], check=False)
        if result.returncode == 0:
            already_recorded = file_path in auto_added_files
            if not already_recorded:
                append_file_path_to_file(auto_added_path, file_path)
                auto_added_files.add(file_path)

            # Get after state
            ls_after = run_git_command(["ls-files", "--stage", "--", file_path], check=False).stdout.strip()
            log_journal(
                "git_add_intent_to_add",
                file_path=file_path,
                index_before=ls_before,
                index_after=ls_after,
                already_recorded=already_recorded,
                returncode=result.returncode,
            )
