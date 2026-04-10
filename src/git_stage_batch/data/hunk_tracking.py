"""Hunk navigation, state management, staleness detection, and progress tracking."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

from ..batch.display import annotate_with_batch_source
from ..core.hashing import compute_stable_hunk_hash
from ..core.models import CurrentLines
from ..core.diff_parser import (
    build_current_lines_from_patch_bytes,
    parse_unified_diff_streaming,
    write_snapshots_for_current_file_path,
)
from ..core.line_selection import write_line_ids_file
from ..exceptions import exit_with_error
from ..i18n import _
from ..output.hunk import print_annotated_hunk_with_aligned_gutter
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command, stream_git_command
from ..utils.paths import (
    get_batched_hunks_file_path,
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_index_snapshot_file_path,
    get_processed_batch_ids_file_path,
    get_processed_include_ids_file_path,
    get_skipped_hunks_jsonl_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_current_lines_to_serializable_dict


def clear_current_hunk_state_files() -> None:
    """Clear all cached current hunk state files."""
    get_current_hunk_patch_file_path().unlink(missing_ok=True)
    get_current_hunk_hash_file_path().unlink(missing_ok=True)
    get_current_lines_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)
    # NOTE: processed_batch_ids is global state (union of all batches), not per-hunk state


def apply_line_level_batch_filter_to_cached_hunk() -> bool:
    """Filter cached hunk to exclude batched lines using stable batch source coordinates.

    Returns:
        True if hunk should be skipped (all lines filtered), False otherwise
    """
    from dataclasses import replace
    from ..core.line_selection import parse_line_selection
    from .line_state import load_current_lines_from_state, convert_current_lines_to_serializable_dict

    current_lines = load_current_lines_from_state()
    if current_lines is None:
        return True

    # Load batch mask (handles both new JSON format and legacy line-ID format)
    mask_path = get_processed_batch_ids_file_path()
    if not mask_path.exists():
        return False  # No mask, no filtering needed

    try:
        mask_content = read_text_file_contents(mask_path)
        if not mask_content:
            return False

        # Try new JSON format first
        if mask_content.strip().startswith('{'):
            try:
                file_mask = json.loads(mask_content)
                if not file_mask:  # Empty dict
                    return False

                # Check if current file is in mask
                file_path = current_lines.path
                if file_path not in file_mask:
                    return False  # File not masked

                file_data = file_mask[file_path]

                # Get claimed source lines and deletion positions from mask
                claimed_lines_ranges = file_data.get("claimed_lines", [])
                deletion_position_ranges = file_data.get("deletion_positions", [])

                if not claimed_lines_ranges and not deletion_position_ranges:
                    return False  # No lines claimed or deletions

                claimed_source_lines = set(parse_line_selection(",".join(claimed_lines_ranges))) if claimed_lines_ranges else set()
                deletion_positions = set(parse_line_selection(",".join(deletion_position_ranges))) if deletion_position_ranges else set()

                # Filter out lines whose source_line is in the mask
                filtered_lines = []
                new_id = 1
                for line_entry in current_lines.lines:
                    # Check if this line's source position is masked
                    if line_entry.source_line is not None:
                        # For deletions (kind == '-'), check deletion positions
                        # For context/additions (kind == ' ' or '+'), check claimed lines
                        if line_entry.kind == '-' and line_entry.source_line in deletion_positions:
                            continue  # Skip batched deletion
                        elif line_entry.kind in (' ', '+') and line_entry.source_line in claimed_source_lines:
                            continue  # Skip batched context/addition
                    # Keep this line with renumbered ID
                    filtered_lines.append(replace(line_entry, id=new_id if line_entry.kind != " " else 0))
                    if line_entry.kind != " ":
                        new_id += 1
            except json.JSONDecodeError:
                # Fall through to legacy format
                pass

        # Legacy format: line IDs separated by newlines (display IDs, not source line positions)
        # This is kept for backward compatibility with old tests
        if 'filtered_lines' not in locals():
            from ..core.line_selection import read_line_ids_file
            batched_ids = set(read_line_ids_file(mask_path))
            if not batched_ids:
                return False  # No filtering needed

            # Filter out batched lines and renumber
            filtered_lines = []
            new_id = 1
            for line_entry in current_lines.lines:
                if line_entry.id in batched_ids:
                    continue  # Skip batched lines
                # Create new line with renumbered ID
                filtered_lines.append(replace(line_entry, id=new_id if line_entry.kind != " " else 0))
                if line_entry.kind != " ":
                    new_id += 1
    except FileNotFoundError:
        return False

    # If all changed lines were batched, skip this hunk
    if not any(line.kind in ("+", "-") for line in filtered_lines):
        return True

    # Create filtered CurrentLines
    filtered_current_lines = CurrentLines(
        path=current_lines.path,
        header=current_lines.header,
        lines=filtered_lines
    )

    # Update cached hunk with filtered version
    write_text_file_contents(get_current_lines_json_file_path(),
                            json.dumps(convert_current_lines_to_serializable_dict(filtered_current_lines),
                                      ensure_ascii=False, indent=0))

    return False


def cache_batch_as_single_hunk(batch_name: str) -> Optional[CurrentLines]:
    """Load entire batch and cache it as a single hunk using batch source model.

    For now, shows only the first file from the batch. Future enhancement
    could combine all files or add batch file navigation.

    Args:
        batch_name: Name of the batch to load

    Returns:
        CurrentLines for the first batch file if found, None if batch is empty
    """
    from ..batch.display import build_display_lines_from_batch_source
    from ..batch.query import read_batch_metadata
    from ..batch.ownership import BatchOwnership
    from ..core.models import CurrentLines, HunkHeader, LineEntry
    from ..utils.git import run_git_command

    # Read batch metadata
    metadata = read_batch_metadata(batch_name)
    files = metadata.get("files", {})

    if not files:
        return None

    # For now, show only the first file
    # TODO: Future enhancement - navigate through all batch files
    file_path = next(iter(files.keys()))
    file_meta = files[file_path]

    # Get batch source commit and ownership
    batch_source_commit = file_meta["batch_source_commit"]
    ownership = BatchOwnership.from_metadata_dict(file_meta)

    # Read batch source content
    batch_source_result = run_git_command(["show", f"{batch_source_commit}:{file_path}"], check=False)
    if batch_source_result.returncode != 0:
        return None
    batch_source_content = batch_source_result.stdout

    # Build display lines (already has correct line IDs matching ownership)
    display_lines = build_display_lines_from_batch_source(batch_source_content, ownership)

    if not display_lines:
        return None

    # Convert to CurrentLines format for display compatibility
    line_entries = []
    new_line_num = 1

    for display_line in display_lines:
        line_id = display_line["id"]
        content = display_line["content"]

        # Convert string content to bytes (encode as UTF-8)
        content_bytes = content.encode('utf-8')
        # Strip only the newline terminator, preserve \r
        text_bytes = content_bytes.rstrip(b'\n')
        # Decode with replacement for display
        text = text_bytes.decode('utf-8', errors='replace')

        if display_line["type"] == "claimed":
            # Claimed line from batch source
            source_line = display_line["source_line"]
            line_entries.append(LineEntry(
                id=line_id,
                kind="+",
                old_line_number=None,
                new_line_number=new_line_num,
                text_bytes=text_bytes,
                text=text,
                source_line=source_line
            ))
        else:  # insertion
            # Insertion (doesn't exist in batch source)
            line_entries.append(LineEntry(
                id=line_id,
                kind="+",
                old_line_number=None,
                new_line_number=new_line_num,
                text_bytes=text_bytes,
                text=text,
                source_line=None
            ))
        new_line_num += 1

    # Create hunk header (all additions, no old content)
    header = HunkHeader(
        old_start=0,
        old_len=0,
        new_start=1,
        new_len=len(line_entries)
    )

    current_lines = CurrentLines(
        path=file_path,
        header=header,
        lines=line_entries
    )

    # Synthesize a patch text for hashing (not used for applying, just for identity)
    patch_lines = [
        f"--- /dev/null",
        f"+++ b/{file_path}",
        f"@@ -0,0 +1,{len(line_entries)} @@"
    ]
    for entry in line_entries:
        patch_lines.append(f"+{entry.text}")
    patch_text = "\n".join(patch_lines) + "\n"

    patch_hash = compute_stable_hunk_hash(patch_text)

    # Cache the hunk
    write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
    write_text_file_contents(get_current_hunk_hash_file_path(), patch_hash)

    # Save CurrentLines for line-level operations
    write_text_file_contents(get_current_lines_json_file_path(),
                            json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                      ensure_ascii=False, indent=0))

    # No snapshots for batch hunks (they don't track staleness)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)

    return current_lines


def find_and_cache_next_unblocked_hunk() -> Optional[CurrentLines]:
    """Find the next hunk that isn't blocked and cache it as current.

    Returns:
        CurrentLines for the hunk if found, None otherwise
    """
    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist (includes current iteration)
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    # Load batched hunks (permanent, survives 'again')
    batched_content = read_text_file_contents(get_batched_hunks_file_path())
    batched_hashes = set(batched_content.splitlines()) if batched_content else set()
    blocked_hashes.update(batched_hashes)

    # Stream git diff and parse incrementally - stops after first unblocked hunk found
    try:
        for single_hunk in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            patch_bytes = single_hunk.to_patch_bytes()
            hunk_hash = compute_stable_hunk_hash(patch_bytes)
            if hunk_hash in blocked_hashes:
                continue

            # Skip hunks from blocked files
            current_lines = build_current_lines_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
            if current_lines.path in blocked_files:
                continue

            # Decode to text for storage (with errors='replace' for non-UTF-8)
            patch_text = patch_bytes.decode('utf-8', errors='replace')
            write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_current_hunk_hash_file_path(), hunk_hash)

            write_text_file_contents(get_current_lines_json_file_path(),
                                     json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                                ensure_ascii=False, indent=0))
            write_snapshots_for_current_file_path(current_lines.path)

            # Apply line-level batch filtering
            if apply_line_level_batch_filter_to_cached_hunk():
                # All lines were batched, skip this hunk and continue
                clear_current_hunk_state_files()
                continue

            # Return filtered hunk (or original if no filtering applied)
            from .line_state import load_current_lines_from_state
            return load_current_lines_from_state()
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    return None


def advance_to_next_hunk() -> None:
    """Clear current hunk state and advance to the next unblocked hunk."""
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def show_current_hunk() -> None:
    """Display the currently cached hunk.

    This is a helper for commands that need to display the cached hunk
    without advancing (e.g., start, again).
    """
    patch_path = get_current_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        patch_bytes = patch_text.encode('utf-8')  # Convert stored text to bytes
        current_lines = build_current_lines_from_patch_bytes(patch_bytes)
        print_annotated_hunk_with_aligned_gutter(current_lines)


def advance_to_and_show_next_hunk() -> None:
    """Advance to next hunk and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk with displaying it. If no more hunks exist, prints
    a message to stderr.
    """
    advance_to_next_hunk()

    # Check if a hunk was cached
    patch_path = get_current_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        patch_bytes = patch_text.encode('utf-8')  # Convert stored text to bytes
        current_lines = build_current_lines_from_patch_bytes(patch_bytes)
        print_annotated_hunk_with_aligned_gutter(current_lines)
    else:
        print(_("No more hunks to process."), file=sys.stderr)


def snapshots_are_stale(file_path: str) -> bool:
    """Check if cached snapshots are stale (file changed since snapshots taken).

    Args:
        file_path: Repository-relative path to check

    Returns:
        True if the file has been committed or otherwise changed such that
        the cached hunk no longer applies
    """
    snapshot_base_path = get_index_snapshot_file_path()
    snapshot_new_path = get_working_tree_snapshot_file_path()

    # Missing snapshots means state is incomplete/stale
    if not snapshot_base_path.exists() or not snapshot_new_path.exists():
        return True

    # Read cached snapshots
    cached_index_content = read_text_file_contents(snapshot_base_path)
    cached_worktree_content = read_text_file_contents(snapshot_new_path)

    # Get current file content from index
    try:
        result = run_git_command(["show", f":{file_path}"], check=False)
        if result.returncode != 0:
            # File not in index (was deleted, or never added)
            current_index_content = ""
        else:
            current_index_content = result.stdout
    except Exception:
        return True  # Error reading means state is stale

    # Get current file content from working tree
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    try:
        current_worktree_content = read_text_file_contents(file_full_path)
    except Exception:
        return True  # Error reading means state is stale

    # Compare snapshots with current state
    return (cached_index_content != current_index_content or
            cached_worktree_content != current_worktree_content)


def require_current_hunk_and_check_stale() -> None:
    """Ensure current hunk exists and is not stale, exit with error otherwise."""
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error(_("No current hunk. Run 'start' first."))

    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error(_("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."))


def recalculate_current_hunk_for_file(file_path: str) -> None:
    """Recalculate the current hunk for a specific file after modifications.

    After discard --line or include --line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.

    Args:
        file_path: Repository-relative path to recalculate hunk for
    """
    from .line_state import load_current_lines_from_state

    # Clear processed IDs since old line numbers don't apply to fresh hunk
    write_line_ids_file(get_processed_include_ids_file_path(), set())

    # Load blocklist
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    # Stream git diff and parse incrementally - stops after first matching hunk found
    try:
        for single_hunk in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            if single_hunk.old_path != file_path and single_hunk.new_path != file_path:
                continue

            patch_bytes = single_hunk.to_patch_bytes()
            hunk_hash = compute_stable_hunk_hash(patch_bytes)

            if hunk_hash in blocked_hashes:
                continue

            # Cache this hunk as current (decode to text for storage)
            patch_text = patch_bytes.decode('utf-8', errors='replace')
            write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_current_hunk_hash_file_path(), hunk_hash)

            current_lines = build_current_lines_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
            write_text_file_contents(get_current_lines_json_file_path(),
                                    json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                              ensure_ascii=False, indent=0))
            write_snapshots_for_current_file_path(current_lines.path)

            # Apply batch filter to exclude batched lines
            if apply_line_level_batch_filter_to_cached_hunk():
                # All lines were batched, clear the hunk
                clear_current_hunk_state_files()
                print(_("No more lines in this hunk."), file=sys.stderr)
                return

            # Display filtered hunk
            current_lines = load_current_lines_from_state()
            if current_lines is not None:
                print_annotated_hunk_with_aligned_gutter(current_lines)
            return
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        clear_current_hunk_state_files()
        print(_("No pending hunks."), file=sys.stderr)
        return

    # No more hunks for this file, advance to next file
    clear_current_hunk_state_files()
    # Import here to avoid circular dependency
    from ..commands.show import command_show
    command_show()


def record_hunk_included(hunk_hash: str) -> None:
    """Record that a hunk was included (staged)."""
    included_path = get_included_hunks_file_path()
    content = read_text_file_contents(included_path)
    existing = set(content.splitlines()) if content else set()
    existing.add(hunk_hash)
    write_text_file_contents(included_path, "\n".join(sorted(existing)) + "\n" if existing else "")


def record_hunk_discarded(hunk_hash: str) -> None:
    """Record that a hunk was discarded (removed from working tree)."""
    discarded_path = get_discarded_hunks_file_path()
    content = read_text_file_contents(discarded_path)
    existing = set(content.splitlines()) if content else set()
    existing.add(hunk_hash)
    write_text_file_contents(discarded_path, "\n".join(sorted(existing)) + "\n" if existing else "")


def record_hunk_skipped(current_lines: CurrentLines, hunk_hash: str) -> None:
    """Record that a hunk was skipped with metadata for display.

    Args:
        current_lines: Current hunk's lines
        hunk_hash: SHA-1 hash of the hunk
    """
    # Extract first changed line number for display
    first_changed_line = None
    for entry in current_lines.lines:
        if entry.kind != " ":  # Not context
            first_changed_line = entry.old_line_number or entry.new_line_number
            break

    # Build metadata object
    metadata = {
        "hash": hunk_hash,
        "file": current_lines.path,
        "line": first_changed_line or 0,
        "ids": current_lines.changed_line_ids()
    }

    # Append to JSONL file
    jsonl_path = get_skipped_hunks_jsonl_file_path()
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(metadata) + "\n")


def format_id_range(ids: list[int]) -> str:
    """Format list of IDs as compact range string (e.g., '1-5,7,9-11').

    Args:
        ids: List of integer IDs

    Returns:
        Compact range string
    """
    if not ids:
        return ""

    ids = sorted(ids)
    ranges = []
    start = ids[0]
    end = ids[0]

    for i in range(1, len(ids)):
        if ids[i] == end + 1:
            end = ids[i]
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = end = ids[i]

    # Add final range
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")

    return ",".join(ranges)
