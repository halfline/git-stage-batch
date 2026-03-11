"""Command implementations for git-stage-batch."""

from __future__ import annotations

import shutil

from .display import print_colored_patch
from .i18n import _
from .parser import parse_unified_diff_into_single_hunk_patches
from .state import ensure_state_directory_exists, get_state_directory_path, require_git_repository, run_git_command


def command_start() -> None:
    """Start a new batch staging session."""
    require_git_repository()
    ensure_state_directory_exists()


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

    # Get the current diff
    result = run_git_command(["diff", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to stage."))
        return

    # Display the first hunk
    first_patch = patches[0]
    print_colored_patch(first_patch.to_patch_text())
