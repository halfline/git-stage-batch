"""Smart quit handling for interactive mode."""

from __future__ import annotations

from ..commands.abort import command_abort
from ..commands.stop import command_stop
from ..data.progress import get_hunk_counts
from ..utils.file_io import read_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import (
    get_start_head_file_path,
    get_start_index_tree_file_path,
)
from .prompts import prompt_quit_session


def handle_quit(*, stop_session: bool = True) -> None:
    """
    Handle quit action with smart quit logic.

    Checks if any changes were made (HEAD, index tree, or discards).
    If no changes, silently stops. If changes exist, prompts user.
    """
    print()

    start_head_file = get_start_head_file_path()
    start_index_tree_file = get_start_index_tree_file_path()

    if not start_head_file.exists() or not start_index_tree_file.exists():
        if stop_session:
            command_stop()
        return

    start_head = read_text_file_contents(start_head_file).strip()
    start_index_tree = read_text_file_contents(start_index_tree_file).strip()

    selected_head = run_git_command(
        ["rev-parse", "HEAD"],
        requires_index_lock=False,
    ).stdout.strip()
    selected_index_tree = run_git_command(
        ["write-tree"],
        requires_index_lock=False,
    ).stdout.strip()

    stats = get_hunk_counts()
    has_discards = stats.get("discarded", 0) > 0

    if (
        selected_head == start_head
        and selected_index_tree == start_index_tree
        and not has_discards
    ):
        if stop_session:
            command_stop()
        return

    choice = prompt_quit_session()

    if choice == "keep":
        if stop_session:
            command_stop()
    elif choice == "undo":
        command_abort()
