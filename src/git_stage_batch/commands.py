"""Command implementations for git-stage-batch."""

from __future__ import annotations

import shutil

from .display import print_colored_patch
from .i18n import _
from .parser import parse_unified_diff_streaming
from .state import (
    ensure_state_directory_exists,
    get_context_lines,
    get_context_lines_file_path,
    get_state_directory_path,
    require_git_repository,
    stream_git_command,
    write_text_file_contents,
)


def command_start(unified: int = 3) -> None:
    """Start a new batch staging session."""
    require_git_repository()
    ensure_state_directory_exists()

    # Save context lines for this session
    write_text_file_contents(get_context_lines_file_path(), str(unified))


def command_stop() -> None:
    """Stop the current batch staging session."""
    require_git_repository()
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    print(_("✓ State cleared."))


def command_again() -> None:
    """Clear state and start a fresh pass through all hunks."""
    require_git_repository()
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    ensure_state_directory_exists()


def command_show() -> None:
    """Show the first available hunk."""
    require_git_repository()

    # Stream diff and show first hunk
    for first_patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        print_colored_patch(first_patch.to_patch_text())
        return

    print(_("No changes to stage."))
