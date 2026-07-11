"""Apply atomic batch executable-mode actions."""

from __future__ import annotations

from pathlib import Path

from ...data.file_modes import apply_git_file_mode
from ...exceptions import CommandError
from ...utils.git_command import run_git_command
from ...utils.git_repository import get_git_repository_root_path


def is_file_mode_action(file_meta: dict) -> bool:
    return file_meta.get("file_type") == "mode"


def _mode(file_meta: dict, field: str) -> str:
    mode = file_meta.get(field)
    if mode not in {"100644", "100755"}:
        raise CommandError(f"Invalid batch file mode: {mode}")
    return mode


def stage_file_mode(file_path: str, file_meta: dict) -> None:
    mode = _mode(file_meta, "new_mode")
    chmod = "+x" if mode == "100755" else "-x"
    result = run_git_command(
        ["update-index", f"--chmod={chmod}", "--", file_path],
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(f"Failed to stage file mode for {file_path}")


def apply_new_file_mode(file_path: str, file_meta: dict) -> None:
    _apply(file_path, _mode(file_meta, "new_mode"))


def apply_old_file_mode(file_path: str, file_meta: dict) -> None:
    _apply(file_path, _mode(file_meta, "old_mode"))


def _apply(file_path: str, mode: str) -> None:
    path: Path = get_git_repository_root_path() / file_path
    if not path.exists() or path.is_symlink():
        raise CommandError(f"Cannot apply executable mode to {file_path}")
    apply_git_file_mode(path, mode)
