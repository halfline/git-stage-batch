"""Command implementations for git-stage-batch."""

from __future__ import annotations

import shutil
import subprocess

from .display import print_colored_patch
from .hashing import compute_stable_hunk_hash
from .i18n import _
from .parser import parse_unified_diff_streaming
from .state import (
    append_lines_to_file,
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_context_lines_file_path,
    get_state_directory_path,
    read_text_file_contents,
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
    """Show the first unprocessed hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Stream diff and show first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            # Display this unprocessed hunk
            print_colored_patch(patch_text)
            return

    # Either no changes or all hunks are blocked
    print(_("No more hunks to process."))


def command_include(*, quiet: bool = False) -> None:
    """Include (stage) the current hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist to skip already-processed hunks
    blocklist_path = get_block_list_file_path()
    if blocklist_path.exists():
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())
    else:
        blocked_hashes = set()

    # Stream diff and find first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash in blocked_hashes:
            continue

        # Extract filename for user feedback
        filename = patch.new_path if patch.new_path else "unknown"

        # Apply the hunk to the index
        try:
            subprocess.run(
                ["git", "apply", "--cached"],
                input=patch_text,
                text=True,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(_("Failed to apply hunk: {}").format(e.stderr))
            return

        # Add hash to blocklist
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk staged from {}").format(filename))
        break

    if not quiet:
        print(_("No more hunks to process."))


def command_skip(*, quiet: bool = False) -> None:
    """Skip the current hunk without staging it."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist to skip already-processed hunks
    blocklist_path = get_block_list_file_path()
    if blocklist_path.exists():
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())
    else:
        blocked_hashes = set()

    # Stream diff and find first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash in blocked_hashes:
            continue

        # Extract filename for user feedback
        filename = patch.new_path if patch.new_path else "unknown"

        # Add hash to blocklist (without staging)
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk skipped from {}").format(filename))
        break

    if not quiet:
        print(_("No more hunks to process."))
