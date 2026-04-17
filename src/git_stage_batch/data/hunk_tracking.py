"""Hunk navigation, state management, staleness detection, and progress tracking."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Generator, Optional, Union

from ..batch.display import annotate_with_batch_source
from ..core.hashing import compute_binary_file_hash, compute_stable_hunk_hash
from ..core.models import BinaryFileChange, LineLevelChange, HunkHeader, LineEntry
from ..core.diff_parser import (
    build_line_changes_from_patch_bytes,
    parse_unified_diff_streaming,
    write_snapshots_for_selected_file_path,
)
from ..core.line_selection import write_line_ids_file
from ..exceptions import CommandError, NoMoreHunks, exit_with_error
from ..i18n import _
from ..output import print_line_level_changes, print_binary_file_change
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command, stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_binary_file_json_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_skipped_hunks_jsonl_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_line_changes_to_serializable_dict


def clear_selected_change_state_files() -> None:
    """Clear all cached selected hunk state files."""
    get_selected_hunk_patch_file_path().unlink(missing_ok=True)
    get_selected_hunk_hash_file_path().unlink(missing_ok=True)
    get_line_changes_json_file_path().unlink(missing_ok=True)
    get_selected_binary_file_json_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)
    # processed_batch_ids is global state (union of all batches), not per-hunk state


def load_selected_binary_file() -> Optional[BinaryFileChange]:
    """Load the currently cached binary file.

    Returns:
        BinaryFileChange if a binary file is cached, None otherwise
    """
    binary_path = get_selected_binary_file_json_path()
    if not binary_path.exists():
        return None

    try:
        binary_data = json.loads(read_text_file_contents(binary_path))
        return BinaryFileChange(
            old_path=binary_data["old_path"],
            new_path=binary_data["new_path"],
            change_type=binary_data["change_type"]
        )
    except (json.JSONDecodeError, KeyError):
        return None


def apply_line_level_batch_filter_to_cached_hunk() -> bool:
    """Filter cached hunk using file-centric ownership attribution.

    File-centric blame-like approach:
    1. Build complete file attribution (all ownership-relevant units + batch owners)
    2. Project attribution onto diff fragments
    3. Filter owned fragments

    Returns:
        True if hunk should be skipped (all lines filtered), False otherwise
    """
    from ..batch.attribution import build_file_attribution, filter_owned_diff_fragments
    from .line_state import load_line_changes_from_state, convert_line_changes_to_serializable_dict

    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return True

    file_path = line_changes.path

    # Step 1: Build file attribution (file-centric, not diff-centric)
    attribution = build_file_attribution(file_path)

    # Step 2 & 3: Project to diff and filter owned fragments
    should_skip, filtered_line_changes = filter_owned_diff_fragments(
        line_changes, attribution
    )

    if should_skip:
        return True

    # Update cached hunk with filtered version
    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(convert_line_changes_to_serializable_dict(filtered_line_changes),
                  ensure_ascii=False, indent=0)
    )

    return False


def render_batch_file_display(batch_name: str, file_path: str, *, metadata=None) -> Optional['RenderedBatchDisplay']:
    """Pure function to render batch file display with gutter ID translation.

    This is a side-effect-free helper that:
    - Reads batch metadata
    - Reads batch source content
    - Reads current working tree content
    - Probes individual line mergeability
    - Builds LineLevelChange with original selection IDs
    - Builds gutter ID mappings

    Does NOT:
    - Write cache files
    - Mutate selected hunk state
    - Compute patch hashes

    Args:
        batch_name: Name of the batch
        file_path: Specific file to render

    Returns:
        RenderedBatchDisplay with line changes and gutter ID translation, or None if file not found.
    """
    from ..batch.display import build_display_lines_from_batch_source
    from ..batch.merge import merge_batch as _merge_batch
    from ..batch.ownership import BatchOwnership, build_ownership_units_from_display
    from ..batch.query import read_batch_metadata
    from ..utils.git import run_git_command, get_git_repository_root_path

    # Read batch metadata (use passed metadata if provided to avoid re-reading)
    if metadata is None:
        metadata = read_batch_metadata(batch_name)
    files = metadata.get("files", {})

    if not files or file_path not in files:
        return None

    file_meta = files[file_path]

    # Get batch source commit and ownership
    batch_source_commit = file_meta["batch_source_commit"]
    ownership = BatchOwnership.from_metadata_dict(file_meta)

    # Read batch source content (as bytes)
    batch_source_result = run_git_command(["show", f"{batch_source_commit}:{file_path}"], check=False, text_output=False)
    if batch_source_result.returncode != 0:
        return None
    batch_source_content_bytes = batch_source_result.stdout
    batch_source_content_str = batch_source_content_bytes.decode('utf-8', errors='replace')

    # Read current working tree content for mergeability probing
    repo_root = get_git_repository_root_path()
    working_path = repo_root / file_path
    if working_path.exists():
        working_content = working_path.read_bytes()
    else:
        working_content = b""

    # Build display lines (already has correct line IDs matching ownership)
    display_lines = build_display_lines_from_batch_source(batch_source_content_str, ownership)

    if not display_lines:
        return None

    # Build ownership units reusing already-built display lines (avoids re-rendering)
    from ..batch.ownership import BatchOwnership as _BatchOwnership
    units = build_ownership_units_from_display(ownership, batch_source_content_bytes, display_lines=display_lines)
    mergeable_display_ids = set()
    for unit in units:
        try:
            # Build unit ownership directly from unit data (no re-rendering)
            unit_ownership = _BatchOwnership(
                claimed_lines=list(str(l) for l in unit.claimed_source_lines),
                deletions=list(unit.deletion_claims),
            )
            _merge_batch(batch_source_content_bytes, unit_ownership, working_content)
            mergeable_display_ids |= unit.display_line_ids
        except Exception:
            pass

    # Convert to LineLevelChange format for display compatibility
    # Keep original selection IDs - mergeability is stored separately
    line_entries = []
    new_line_num = 1

    for display_line in display_lines:
        line_id = display_line["id"]  # Keep original selection ID
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
            new_line_num += 1
        elif display_line["type"] == "deletion":
            # Deletion (suppression constraint - show as deletion for display)
            line_entries.append(LineEntry(
                id=line_id,
                kind="-",
                old_line_number=None,  # Not from old file (it's a constraint)
                new_line_number=None,
                text_bytes=text_bytes,
                text=text,
                source_line=None
            ))

    # Compute header based on actual line types
    addition_count = sum(1 for e in line_entries if e.kind == "+")
    deletion_count = sum(1 for e in line_entries if e.kind == "-")

    # Create hunk header
    header = HunkHeader(
        old_start=0 if deletion_count == 0 else 1,
        old_len=deletion_count,
        new_start=0 if addition_count == 0 else 1,
        new_len=addition_count
    )

    line_changes = LineLevelChange(
        path=file_path,
        header=header,
        lines=line_entries
    )

    # Build gutter ID mappings
    # All display lines in mergeable units get consecutive gutter IDs (1, 2, 3...)
    gutter_to_selection_id = {}
    selection_id_to_gutter = {}
    gutter_num = 1
    for entry in line_entries:
        if entry.id is not None and entry.id in mergeable_display_ids:
            gutter_to_selection_id[gutter_num] = entry.id
            selection_id_to_gutter[entry.id] = gutter_num
            gutter_num += 1

    from ..core.models import RenderedBatchDisplay
    return RenderedBatchDisplay(
        line_changes=line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
        selection_id_to_gutter=selection_id_to_gutter
    )


def write_hunk_cache_from_rendered(rendered: 'RenderedBatchDisplay') -> None:
    """Write hunk cache files from an already-rendered batch display.

    Extracts the caching side effects from cache_batch_as_single_hunk so
    callers that already have a rendered display don't need to re-render.

    Args:
        rendered: Already-rendered batch display to cache
    """
    line_changes = rendered.line_changes
    line_entries = line_changes.lines
    header = line_changes.header
    file_path = line_changes.path

    # Compute counts for patch synthesis
    addition_count = sum(1 for e in line_entries if e.kind == "+")
    deletion_count = sum(1 for e in line_entries if e.kind == "-")

    # Synthesize a patch for hashing and caching (preserving original bytes)
    old_path = "/dev/null" if deletion_count == 0 and addition_count > 0 else f"a/{file_path}"
    new_path = "/dev/null" if addition_count == 0 and deletion_count > 0 else f"b/{file_path}"

    patch_bytes_parts = [
        f"--- {old_path}\n".encode('utf-8'),
        f"+++ {new_path}\n".encode('utf-8'),
        f"@@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@\n".encode('utf-8')
    ]
    for entry in line_entries:
        patch_bytes_parts.append(entry.kind.encode('utf-8') + entry.text_bytes + b'\n')
    patch_bytes = b"".join(patch_bytes_parts)

    patch_hash = compute_stable_hunk_hash(patch_bytes)

    # Cache the hunk (decode with replacement for text storage)
    patch_text = patch_bytes.decode('utf-8', errors='replace')
    write_text_file_contents(get_selected_hunk_patch_file_path(), patch_text)
    write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)

    # Save LineLevelChange for line-level operations
    write_text_file_contents(get_line_changes_json_file_path(),
                            json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                      ensure_ascii=False, indent=0))

    # No snapshots for batch hunks (they don't track staleness)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)


def cache_batch_as_single_hunk(batch_name: str, file_path: str | None = None) -> Optional['RenderedBatchDisplay']:
    """Load file from batch and cache it as a single hunk using batch source model.

    Args:
        batch_name: Name of the batch to load
        file_path: Specific file to cache, or None for first file

    Returns:
        RenderedBatchDisplay with line changes and gutter ID translation, or None if batch is empty or file not found.
        The gutter_to_selection_id mapping translates user-visible filtered gutter IDs (1, 2, 3...)
        to original selection IDs for ownership selection commands.
    """
    from ..batch.query import read_batch_metadata

    # Read batch metadata
    metadata = read_batch_metadata(batch_name)
    files = metadata.get("files", {})

    if not files:
        return None

    # Determine which file to use
    if file_path is None:
        # Default to first file (sorted order for consistency)
        file_path = sorted(files.keys())[0]
    elif file_path not in files:
        # Requested file not in batch
        raise CommandError(f"File '{file_path}' not found in batch '{batch_name}'")

    # Use pure render helper (side-effect free)
    rendered = render_batch_file_display(batch_name, file_path)
    if rendered is None:
        return None

    write_hunk_cache_from_rendered(rendered)
    return rendered


def cache_batch_files_generator(batch_name: str) -> Generator['RenderedBatchDisplay', None, None]:
    """Yield RenderedBatchDisplay for each file in batch.

    Files are yielded in sorted order. Each file has line IDs
    from original display IDs. Batch content comes from batch storage (not working tree).

    Args:
        batch_name: Name of the batch

    Yields:
        RenderedBatchDisplay for each file in batch with gutter ID translation.
    """
    from ..batch.query import read_batch_metadata

    # Read batch metadata
    metadata = read_batch_metadata(batch_name)
    files = sorted(metadata.get("files", {}).keys())

    for file_path in files:
        # Use pure render helper (side-effect free)
        rendered = render_batch_file_display(batch_name, file_path)
        if rendered is not None:
            yield rendered


def get_batch_file_for_line_operation(batch_name: str, file: str | None) -> str:
    """Determine which file in batch to operate on.

    Args:
        batch_name: Name of batch
        file: User-specified file path, or None for default

    Returns:
        File path to use

    Raises:
        CommandError: If batch empty or file not in batch
    """
    from ..batch.query import read_batch_metadata

    metadata = read_batch_metadata(batch_name)
    files = sorted(metadata.get("files", {}).keys())

    if not files:
        raise CommandError(f"Batch '{batch_name}' is empty")

    if file is None:
        # Default to first file (sorted order)
        return files[0]

    if file not in files:
        raise CommandError(f"File '{file}' not found in batch '{batch_name}'")

    return file


def cache_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Cache all changes for a file as a single concatenated hunk.

    Reads the CURRENT working tree state for the file and fetches ALL
    hunks (ignoring blocklist/batches), concatenating them into one
    LineLevelChange with continuous line IDs.

    This always reflects the live working tree state, unlike regular
    hunk caching which uses snapshots.

    Args:
        file_path: Repository-relative path to file

    Returns:
        LineLevelChange with all file changes, or None if no changes
    """
    # Get diff for entire file from selected working tree
    # Using git diff HEAD -- file_path to get live state
    try:
        all_line_entries = []
        line_id_counter = 1
        min_old_start = None
        max_old_end = None
        min_new_start = None
        max_new_end = None

        for single_hunk in parse_unified_diff_streaming(
            stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color", "HEAD", "--", file_path])
        ):
            # Build LineLevelChange from this hunk
            patch_bytes = single_hunk.to_patch_bytes()
            line_changes = build_line_changes_from_patch_bytes(patch_bytes)

            # Track bounds for combined header
            if min_old_start is None:
                min_old_start = line_changes.header.old_start
                min_new_start = line_changes.header.new_start

            max_old_end = line_changes.header.old_start + line_changes.header.old_len
            max_new_end = line_changes.header.new_start + line_changes.header.new_len

            # Renumber line IDs to be continuous across all hunks
            for line_entry in line_changes.lines:
                if line_entry.kind != " ":
                    # Changed line: assign new continuous ID
                    new_entry = LineEntry(
                        id=line_id_counter,
                        kind=line_entry.kind,
                        old_line_number=line_entry.old_line_number,
                        new_line_number=line_entry.new_line_number,
                        text_bytes=line_entry.text_bytes,
                        text=line_entry.text,
                        source_line=line_entry.source_line
                    )
                    line_id_counter += 1
                else:
                    # Context line: keep None
                    new_entry = LineEntry(
                        id=None,
                        kind=line_entry.kind,
                        old_line_number=line_entry.old_line_number,
                        new_line_number=line_entry.new_line_number,
                        text_bytes=line_entry.text_bytes,
                        text=line_entry.text,
                        source_line=line_entry.source_line
                    )
                all_line_entries.append(new_entry)

        if not all_line_entries:
            return None

        # Create combined header spanning all hunks
        combined_header = HunkHeader(
            old_start=min_old_start,
            old_len=max_old_end - min_old_start,
            new_start=min_new_start,
            new_len=max_new_end - min_new_start
        )

        combined_line_changes = LineLevelChange(
            path=file_path,
            header=combined_header,
            lines=all_line_entries
        )

        # Synthesize patch text for caching (used for hashing/identity)
        patch_lines = [
            f"--- a/{file_path}",
            f"+++ b/{file_path}",
            f"@@ -{combined_header.old_start},{combined_header.old_len} +{combined_header.new_start},{combined_header.new_len} @@"
        ]
        for entry in all_line_entries:
            patch_lines.append(f"{entry.kind}{entry.text}")
        patch_text = "\n".join(patch_lines) + "\n"

        patch_hash = compute_stable_hunk_hash(patch_text.encode('utf-8'))

        # Cache the combined hunk
        write_text_file_contents(get_selected_hunk_patch_file_path(), patch_text)
        write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
        write_text_file_contents(get_line_changes_json_file_path(),
                                json.dumps(convert_line_changes_to_serializable_dict(combined_line_changes),
                                          ensure_ascii=False, indent=0))

        # No snapshots for file-scoped hunks (they use live state)
        get_index_snapshot_file_path().unlink(missing_ok=True)
        get_working_tree_snapshot_file_path().unlink(missing_ok=True)

        return combined_line_changes

    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes in file)
        return None


def fetch_next_change() -> Union[LineLevelChange, BinaryFileChange]:
    """Find the next hunk or binary file that isn't blocked and cache it as selected.

    Returns:
        LineLevelChange for text hunks, BinaryFileChange for binary files.

    Raises:
        NoMoreHunks: When there are no more items to process.
    """
    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist (includes selected iteration)
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    # Stream git diff and parse incrementally - stops after first unblocked item found
    try:
        for item in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            # Handle binary files
            if isinstance(item, BinaryFileChange):
                binary_hash = compute_binary_file_hash(item)
                if binary_hash in blocked_hashes:
                    continue

                # Determine file path for blocked files check
                file_path = item.new_path if item.new_path != "/dev/null" else item.old_path
                if file_path in blocked_files:
                    continue

                # Cache binary file as JSON (for state persistence)
                binary_data = {
                    "old_path": item.old_path,
                    "new_path": item.new_path,
                    "change_type": item.change_type,
                }
                write_text_file_contents(get_selected_binary_file_json_path(),
                                       json.dumps(binary_data, ensure_ascii=False, indent=0))
                write_text_file_contents(get_selected_hunk_hash_file_path(), binary_hash)

                # Return the BinaryFileChange object directly
                return item

            # Handle text hunks (SingleHunkPatch)
            patch_bytes = item.to_patch_bytes()
            hunk_hash = compute_stable_hunk_hash(patch_bytes)
            if hunk_hash in blocked_hashes:
                continue

            # Skip hunks from blocked files
            line_changes = build_line_changes_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
            if line_changes.path in blocked_files:
                continue

            # Decode to text for storage (with errors='replace' for non-UTF-8)
            patch_text = patch_bytes.decode('utf-8', errors='replace')
            write_text_file_contents(get_selected_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)

            write_text_file_contents(get_line_changes_json_file_path(),
                                     json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                                ensure_ascii=False, indent=0))
            write_snapshots_for_selected_file_path(line_changes.path)

            # Apply line-level batch filtering
            if apply_line_level_batch_filter_to_cached_hunk():
                # All lines were batched, skip this hunk and continue
                clear_selected_change_state_files()
                continue

            # Return filtered hunk (or original if no filtering applied)
            from .line_state import load_line_changes_from_state
            return load_line_changes_from_state()
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    # No more items to process
    raise NoMoreHunks()


def advance_to_next_change() -> None:
    """Clear selected hunk state and advance to the next unblocked hunk.

    If no more hunks exist, clears state and returns silently.
    """
    clear_selected_change_state_files()
    try:
        fetch_next_change()
    except NoMoreHunks:
        # No more items - state is already cleared
        pass


def show_selected_change() -> None:
    """Display the currently cached hunk or binary file.

    This is a helper for commands that need to display the cached hunk
    without advancing (e.g., start, again).
    """
    # Check if selected item is a binary file
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        print_binary_file_change(binary_file)
        return

    # Otherwise, show text hunk
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        patch_bytes = patch_text.encode('utf-8')  # Convert stored text to bytes
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)
        print_line_level_changes(line_changes)


def advance_to_and_show_next_change() -> None:
    """Advance to next hunk/binary file and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk/binary file with displaying it. If no more items exist,
    prints a message to stderr.
    """
    advance_to_next_change()

    # Check if a binary file was cached
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        print_binary_file_change(binary_file)
        return

    # Check if a text hunk was cached
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        patch_bytes = patch_text.encode('utf-8')  # Convert stored text to bytes
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)
        print_line_level_changes(line_changes)
    else:
        print(_("No more hunks to process."), file=sys.stderr)


def get_selected_change_file_path() -> Optional[str]:
    """Get the file path of the currently cached hunk.

    Returns:
        Repository-relative path if a hunk is selected, None otherwise
    """
    json_path = get_line_changes_json_file_path()
    if not json_path.exists():
        return None
    try:
        data = json.loads(read_text_file_contents(json_path))
        return data.get("path")
    except Exception:
        return None


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

    # Get selected file content from index
    try:
        result = run_git_command(["show", f":{file_path}"], check=False)
        if result.returncode != 0:
            # File not in index (was deleted, or never added)
            selected_index_content = ""
        else:
            selected_index_content = result.stdout
    except Exception:
        return True  # Error reading means state is stale

    # Get selected file content from working tree
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    try:
        selected_worktree_content = read_text_file_contents(file_full_path)
    except Exception:
        return True  # Error reading means state is stale

    # Compare snapshots with selected state
    return (cached_index_content != selected_index_content or
            cached_worktree_content != selected_worktree_content)


def require_selected_hunk() -> None:
    """Ensure selected hunk exists and is not stale, exit with error otherwise."""
    if not get_selected_hunk_patch_file_path().exists():
        exit_with_error(_("No selected hunk. Run 'start' first."))

    if get_line_changes_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_line_changes_json_file_path()))
        file_path = data["path"]
        if snapshots_are_stale(file_path):
            clear_selected_change_state_files()
            exit_with_error(_("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."))


def recalculate_selected_hunk_for_file(file_path: str) -> None:
    """Recalculate the selected hunk for a specific file after modifications.

    After discard --line or include --line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.

    Args:
        file_path: Repository-relative path to recalculate hunk for
    """
    from .line_state import load_line_changes_from_state

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

            # Cache this hunk as selected (decode to text for storage)
            patch_text = patch_bytes.decode('utf-8', errors='replace')
            write_text_file_contents(get_selected_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)

            line_changes = build_line_changes_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
            write_text_file_contents(get_line_changes_json_file_path(),
                                    json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                              ensure_ascii=False, indent=0))
            write_snapshots_for_selected_file_path(line_changes.path)

            # Apply batch filter to exclude batched lines
            if apply_line_level_batch_filter_to_cached_hunk():
                # All lines were batched, clear the hunk
                clear_selected_change_state_files()
                print(_("No more lines in this hunk."), file=sys.stderr)
                return

            # Display filtered hunk
            line_changes = load_line_changes_from_state()
            if line_changes is not None:
                print_line_level_changes(line_changes)
            return
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        clear_selected_change_state_files()
        print(_("No pending hunks."), file=sys.stderr)
        return

    # No more hunks for this file, advance to next file
    clear_selected_change_state_files()
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


def record_hunk_skipped(line_changes: LineLevelChange, hunk_hash: str) -> None:
    """Record that a hunk was skipped with metadata for display.

    Args:
        line_changes: Current hunk's lines
        hunk_hash: SHA-1 hash of the hunk
    """
    # Extract first changed line number for display
    first_changed_line = None
    for entry in line_changes.lines:
        if entry.kind != " ":  # Not context
            first_changed_line = entry.old_line_number or entry.new_line_number
            break

    # Build metadata object
    metadata = {
        "hash": hunk_hash,
        "file": line_changes.path,
        "line": first_changed_line or 0,
        "ids": line_changes.changed_line_ids()
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
