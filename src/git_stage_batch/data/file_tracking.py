"""Auto-add untracked files management."""

from __future__ import annotations

import sys
from collections.abc import Iterable

from ..utils.file_io import read_file_paths_file, write_file_paths_file
from ..utils.git_command import run_git_command
from ..git_paths import decode_path, nul_records
from ..utils.git_index import git_add_paths_from_stdin
from ..utils.git_repository import get_git_repository_root_path
from ..utils.journal import log_journal
from ..utils.paths import get_auto_added_files_file_path
from ..i18n import _


UNTRACKED_PROGRESS_THRESHOLD = 1_000


def _embedded_git_repository_index_path(file_path: str) -> str | None:
    normalized_path = file_path.rstrip("/")
    if not normalized_path:
        return None

    path = get_git_repository_root_path() / normalized_path
    if path.is_dir() and (path / ".git").exists():
        return normalized_path
    return None


def list_untracked_files(paths: Iterable[str] | None = None) -> list[str]:
    """Return untracked, non-ignored files, optionally limited to paths."""
    arguments = ["ls-files", "-z", "--others", "--exclude-standard"]
    if paths is not None:
        unique_paths = list(dict.fromkeys(paths))
        if not unique_paths:
            return []
        arguments.extend(["--", *unique_paths])

    result = run_git_command(
        arguments,
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return []

    return [decode_path(path) for path in nul_records(result.stdout)]


def auto_add_untracked_files(
    paths: Iterable[str] | None = None,
    *,
    show_progress: bool = False,
) -> None:
    """Automatically run git add -N on untracked files (except blocked ones).

    This makes untracked files visible to git diff without staging their
    content, enabling the interactive staging workflow for new files.
    Files matching .gitignore patterns are automatically excluded.
    """
    untracked_files = list_untracked_files(paths)
    if not untracked_files:
        return

    untracked_files = list(dict.fromkeys(untracked_files))
    index_paths = list(
        dict.fromkeys(
            _embedded_git_repository_index_path(file_path) or file_path
            for file_path in untracked_files
        )
    )
    if show_progress and len(index_paths) >= UNTRACKED_PROGRESS_THRESHOLD:
        print(
            _("Preparing {count} untracked paths for review...").format(
                count=len(index_paths),
            ),
            file=sys.stderr,
        )
    auto_added_path = get_auto_added_files_file_path()
    recorded_paths = read_file_paths_file(auto_added_path)
    recorded_set = set(recorded_paths)

    def apply_transition(candidate_paths: list[str]):
        new_paths = [path for path in candidate_paths if path not in recorded_set]
        manifest_existed = auto_added_path.exists()
        if new_paths:
            write_file_paths_file(auto_added_path, [*recorded_paths, *new_paths])
        result = git_add_paths_from_stdin(
            candidate_paths,
            intent_to_add=True,
            check=False,
        )
        if result.returncode != 0:
            if new_paths:
                if manifest_existed:
                    write_file_paths_file(auto_added_path, recorded_paths)
                else:
                    auto_added_path.unlink(missing_ok=True)
        return result, new_paths

    result, new_paths = apply_transition(index_paths)
    if result.returncode != 0:
        # A candidate can disappear between discovery and index update. Refresh
        # once and retry the complete surviving transition atomically.
        remaining_files = list_untracked_files(paths)
        index_paths = list(
            dict.fromkeys(
                _embedded_git_repository_index_path(file_path) or file_path
                for file_path in remaining_files
            )
        )
        result, new_paths = apply_transition(index_paths)
    if result.returncode != 0:
        log_journal(
            "git_add_intent_to_add_batch_failed",
            candidate_count=len(index_paths),
            returncode=result.returncode,
        )
        return

    log_journal(
        "git_add_intent_to_add_batch",
        candidate_count=len(index_paths),
        newly_recorded_count=len(new_paths),
        already_recorded_count=len(index_paths) - len(new_paths),
        returncode=result.returncode,
    )
