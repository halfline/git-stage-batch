"""Discard command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..core.diff_parser import build_current_lines_from_patch_text, get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection
from ..data.hunk_tracking import (
    advance_to_next_hunk,
    recalculate_current_hunk_for_file,
    record_hunk_discarded,
    require_current_hunk_and_check_stale,
)
from ..data.line_state import load_current_lines_from_state
from ..data.session import snapshot_file_if_untracked
from ..exceptions import exit_with_error
from ..i18n import _
from ..staging.operations import build_target_working_tree_content_with_discarded_lines
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import get_git_repository_root_path, require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
)


def command_discard(*, quiet: bool = False) -> None:
    """Discard the current hunk from the working tree."""
    from ..data.hunk_tracking import find_and_cache_next_unblocked_hunk

    require_git_repository()
    ensure_state_directory_exists()

    # Ensure cached hunk is fresh (handles case where file was modified externally)
    if find_and_cache_next_unblocked_hunk(quiet=quiet) is None:
        return

    # Read cached hunk
    patch_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    patch_text = read_text_file_contents(get_current_hunk_patch_file_path())

    # Extract filename for user feedback and snapshotting
    current_lines = build_current_lines_from_patch_text(patch_text)
    filename = current_lines.path

    # Snapshot file if untracked before discarding
    if filename != "/dev/null":
        snapshot_file_if_untracked(filename)

    # Apply the hunk in reverse to discard from working tree
    try:
        subprocess.run(
            ["git", "apply", "--reverse"],
            input=patch_text,
            text=True,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(_("Failed to discard hunk: {}").format(e.stderr), file=sys.stderr)
        return

    # After reverse-applying a new file, delete it if it became empty
    # (git apply -R on new files empties them but doesn't delete them)
    is_new_file = "--- /dev/null" in patch_text
    if is_new_file:
        absolute_path = get_git_repository_root_path() / filename
        if absolute_path.exists():
            content = read_text_file_contents(absolute_path)
            if not content.strip():  # File is empty
                absolute_path.unlink()

    # Add hash to blocklist
    blocklist_path = get_block_list_file_path()
    append_lines_to_file(blocklist_path, [patch_hash])

    # Record for progress tracking
    record_hunk_discarded(patch_hash)

    if not quiet:
        print(_("✓ Hunk discarded from {file}").format(file=filename), file=sys.stderr)

    advance_to_next_hunk(quiet=quiet)


def command_discard_file() -> None:
    """Discard the entire current file from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk to get the target file
    def is_unblocked(patch_text: str) -> bool:
        return compute_stable_hunk_hash(patch_text) not in blocked_hashes

    target_file = get_first_matching_file_from_diff(
        context_lines=get_context_lines(),
        predicate=is_unblocked
    )

    if target_file is None:
        print(_("No changes to discard."), file=sys.stderr)
        return

    # Snapshot the file if it's untracked (for abort functionality)
    snapshot_file_if_untracked(target_file)

    # Stream through hunks and collect hashes from target file BEFORE removing it
    # (git rm -f will stage the deletion, making hunks disappear from git diff)
    hashes_to_block = []
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        if patch.new_path != target_file:
            continue

        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash not in blocked_hashes:
            hashes_to_block.append(patch_hash)

    # Remove the file from working tree
    try:
        subprocess.run(
            ["git", "rm", "-f", target_file],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(_("Failed to discard file: {}").format(e.stderr.decode()), file=sys.stderr)
        return

    # Mark all collected hashes as processed
    for patch_hash in hashes_to_block:
        append_lines_to_file(blocklist_path, [patch_hash])

    print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)

    advance_to_next_hunk()


def command_discard_line(line_id_specification: str) -> None:
    """Discard only the specified lines from the working tree.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    ensure_state_directory_exists()
    require_current_hunk_and_check_stale()

    requested_ids = parse_line_selection(line_id_specification)
    current_lines = load_current_lines_from_state()

    # Get current working tree content
    working_file_path = get_git_repository_root_path() / current_lines.path
    if working_file_path.exists():
        working_text = working_file_path.read_text(encoding="utf-8", errors="surrogateescape")
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=current_lines.path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_with_discarded_lines(
        current_lines, set(requested_ids), working_text)

    # Write back to working tree
    working_file_path.write_text(target_working_content, encoding="utf-8", errors="surrogateescape")

    # After modifying working tree, recalculate hunk for the SAME file
    recalculate_current_hunk_for_file(current_lines.path)

    print(_("✓ Discarded line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)
