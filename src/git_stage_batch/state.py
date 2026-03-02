"""State management and filesystem utilities for git-stage-batch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable


# --------------------------- Utility: git and filesystem ---------------------------

def run_git_command(arguments: list[str],
                    check: bool = True,
                    text_output: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *arguments], check=check, text=text_output, capture_output=True)

def require_git_repository() -> None:
    try:
        run_git_command(["rev-parse", "--git-dir"])
    except subprocess.CalledProcessError:
        exit_with_error("Not inside a git repository.")

def get_git_repository_root_path() -> Path:
    output = run_git_command(["rev-parse", "--show-toplevel"]).stdout.strip()
    return Path(output)

def exit_with_error(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
    sys.exit(exit_code)

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


# --------------------------- State paths ---------------------------

def get_state_directory_path() -> Path:
    return get_git_repository_root_path() / ".git" / "git-stage-batch"

def get_block_list_file_path() -> Path:
    return get_state_directory_path() / "blocklist"

def get_current_hunk_patch_file_path() -> Path:
    return get_state_directory_path() / "current-hunk.patch"

def get_current_hunk_hash_file_path() -> Path:
    return get_state_directory_path() / "current.hash"

def get_current_lines_json_file_path() -> Path:
    return get_state_directory_path() / "current-lines.json"

def get_processed_include_ids_file_path() -> Path:
    return get_state_directory_path() / "processed.include"

def get_processed_exclude_ids_file_path() -> Path:
    return get_state_directory_path() / "processed.exclude"

def get_index_snapshot_file_path() -> Path:
    return get_state_directory_path() / "snapshot-base"  # index side

def get_working_tree_snapshot_file_path() -> Path:
    return get_state_directory_path() / "snapshot-new"   # working tree side


def ensure_state_directory_exists() -> None:
    get_state_directory_path().mkdir(parents=True, exist_ok=True)
    get_block_list_file_path().touch(exist_ok=True)

def clear_current_hunk_state_files() -> None:
    for path in (
        get_current_hunk_patch_file_path(),
        get_current_hunk_hash_file_path(),
        get_current_lines_json_file_path(),
        get_processed_include_ids_file_path(),
        get_processed_exclude_ids_file_path(),
        get_index_snapshot_file_path(),
        get_working_tree_snapshot_file_path(),
    ):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
