"""Hunk navigation, state management, staleness detection, and progress tracking."""

from __future__ import annotations

import json
import tempfile
import subprocess
import sys
from dataclasses import replace
from enum import Enum
from typing import Generator, Optional, Union

from ..batch.attribution import build_file_attribution, filter_owned_diff_fragments
from ..batch import display as batch_display
from ..batch import merge as batch_merge
from ..batch.display import annotate_with_batch_source
from ..batch.ownership import (
    BatchOwnership,
    build_ownership_units_from_display_lines,
    rebuild_ownership_from_units,
    validate_ownership_units,
)
from ..batch.query import read_batch_metadata
from ..core.hashing import compute_binary_file_hash, compute_stable_hunk_hash
from ..core.models import BinaryFileChange, LineLevelChange, HunkHeader, LineEntry, RenderedBatchDisplay
from ..core.diff_parser import (
    build_line_changes_from_patch_bytes,
    parse_unified_diff_streaming,
    write_snapshots_for_selected_file_path,
)
from ..core.line_selection import write_line_ids_file
from ..exceptions import CommandError, MergeError, NoMoreHunks, exit_with_error
from ..i18n import _, ngettext
from ..output import print_line_level_changes, print_binary_file_change
from .consumed_selections import read_consumed_file_metadata
from ..utils.file_io import (
    read_file_bytes,
    read_file_paths_file,
    read_text_file_contents,
    write_file_bytes,
    write_text_file_contents,
)
from ..utils.git import get_git_repository_root_path, run_git_command, stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_change_clear_reason_file_path,
    get_selected_change_kind_file_path,
    get_selected_binary_file_json_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_skipped_hunks_jsonl_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state


class SelectedChangeKind(str, Enum):
    """Kinds of selected changes cached in session state."""

    HUNK = "hunk"
    FILE = "file"
    BINARY = "binary"
    BATCH_FILE = "batch-file"


class SelectedChangeClearReason(str, Enum):
    """Reasons selected change state was intentionally cleared."""

    FILE_LIST = "file-list"


def _selected_change_state_paths():
    """Return files that make up the cached selected change state."""
    return {
        "patch": get_selected_hunk_patch_file_path(),
        "hash": get_selected_hunk_hash_file_path(),
        "clear_reason": get_selected_change_clear_reason_file_path(),
        "kind": get_selected_change_kind_file_path(),
        "line_state": get_line_changes_json_file_path(),
        "binary": get_selected_binary_file_json_path(),
        "index_snapshot": get_index_snapshot_file_path(),
        "working_snapshot": get_working_tree_snapshot_file_path(),
        "processed_include_ids": get_processed_include_ids_file_path(),
        "processed_skip_ids": get_processed_skip_ids_file_path(),
    }


def snapshot_selected_change_state() -> dict[str, bytes | None]:
    """Capture the current selected change cache."""
    return {
        name: (read_file_bytes(path) if path.exists() else None)
        for name, path in _selected_change_state_paths().items()
    }


def restore_selected_change_state(snapshot: dict[str, bytes | None]) -> None:
    """Restore a previously captured selected change cache."""
    for name, path in _selected_change_state_paths().items():
        data = snapshot.get(name)
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def clear_selected_change_state_files() -> None:
    """Clear all cached selected hunk state files."""
    from .file_review_state import clear_last_file_review_state

    for path in _selected_change_state_paths().values():
        path.unlink(missing_ok=True)
    clear_last_file_review_state()
    # processed_batch_ids is global state (union of all batches), not per-hunk state


def mark_selected_change_cleared_by_file_list(
    *,
    source: str,
    batch_name: str | None = None,
) -> None:
    """Record that a navigational file list intentionally cleared selection."""
    _write_selected_change_clear_reason(
        reason=SelectedChangeClearReason.FILE_LIST,
        source=source,
        batch_name=batch_name,
    )


def _write_selected_change_clear_reason(
    *,
    reason: SelectedChangeClearReason,
    source: str,
    batch_name: str | None = None,
    file_path: str | None = None,
) -> None:
    """Write a structured selected-change clear marker."""
    write_text_file_contents(
        get_selected_change_clear_reason_file_path(),
        json.dumps(
            {
                "reason": reason.value,
                "source": source,
                "batch_name": batch_name,
                "file_path": file_path,
            },
            ensure_ascii=False,
            indent=0,
        ),
    )


def _read_selected_change_clear_reason() -> dict[str, str | None] | None:
    """Return the structured clear marker, tolerating legacy plain-text state."""
    raw_reason = read_text_file_contents(get_selected_change_clear_reason_file_path()).strip()
    if not raw_reason:
        return None
    if raw_reason == SelectedChangeClearReason.FILE_LIST.value:
        return {
            "reason": SelectedChangeClearReason.FILE_LIST.value,
            "source": None,
            "batch_name": None,
        }
    try:
        data = json.loads(raw_reason)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    reason = data.get("reason")
    if reason not in {item.value for item in SelectedChangeClearReason}:
        return None
    return {
        "reason": reason,
        "source": data.get("source") if isinstance(data.get("source"), str) else None,
        "batch_name": data.get("batch_name") if isinstance(data.get("batch_name"), str) else None,
        "file_path": data.get("file_path") if isinstance(data.get("file_path"), str) else None,
    }


def selected_change_was_cleared_by_file_list(
    *,
    source: str | None = None,
    batch_name: str | None = None,
) -> bool:
    """Return whether the current empty selection came from a file list."""
    if read_selected_change_kind() is not None:
        return False
    marker = _read_selected_change_clear_reason()
    if marker is None:
        return False
    if marker["reason"] != SelectedChangeClearReason.FILE_LIST.value:
        return False
    marker_source = marker["source"]
    marker_batch_name = marker["batch_name"]
    if source is not None and marker_source != source:
        return False
    if batch_name is not None and marker_batch_name != batch_name:
        return False
    return True


def refuse_bare_action_after_file_list(
    action_command: str,
    *,
    open_command: str = "git-stage-batch show --file PATH",
    source: str | None = None,
    batch_name: str | None = None,
) -> None:
    """Refuse a bare action after a navigational file list cleared selection."""
    if not selected_change_was_cleared_by_file_list(source=source, batch_name=batch_name):
        return
    raise CommandError(
        _(
            "No selected change.\n"
            "The last command only showed files; it did not choose one for follow-up actions.\n\n"
            "Run:\n"
            "  git-stage-batch show\n"
            "or choose a file with:\n"
            "  {open_command}\n"
            "before running:\n"
            "  git-stage-batch {action}"
        ).format(open_command=open_command, action=action_command)
    )


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


def write_selected_change_kind(kind: SelectedChangeKind) -> None:
    """Persist the kind of selected change cached in session state."""
    write_text_file_contents(get_selected_change_kind_file_path(), kind)


def read_selected_change_kind() -> Optional[SelectedChangeKind]:
    """Return the kind of selected change cached in session state."""
    path = get_selected_change_kind_file_path()
    if not path.exists():
        return None

    raw_kind = read_text_file_contents(path).strip()
    if not raw_kind:
        return None

    try:
        return SelectedChangeKind(raw_kind)
    except ValueError:
        return None


def load_selected_change() -> Optional[Union[LineLevelChange, BinaryFileChange]]:
    """Load the currently cached selected change, if any."""
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        return binary_file

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    require_selected_hunk()

    line_changes = load_line_changes_from_state()
    if line_changes is not None:
        return line_changes

    patch_bytes = read_file_bytes(patch_path)
    return build_line_changes_from_patch_bytes(patch_bytes)


def get_selected_change_file_path() -> Optional[str]:
    """Return the file path for the currently cached selected change.

    The selected patch/binary cache is the source of truth for pathless
    file-scoped commands because it is what navigation just displayed.
    selected-lines.json is derived state and may lag after display/navigation
    edge cases.
    """
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        return binary_file.new_path if binary_file.new_path != "/dev/null" else binary_file.old_path

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    patch_bytes = read_file_bytes(patch_path)
    line_changes = build_line_changes_from_patch_bytes(patch_bytes)
    return line_changes.path


def apply_line_level_batch_filter_to_cached_hunk() -> bool:
    """Filter cached hunk using file-centric ownership attribution.

    File-centric blame-like approach:
    1. Build complete file attribution (all ownership-relevant units + batch owners)
    2. Project attribution onto diff fragments
    3. Filter owned fragments

    Returns:
        True if hunk should be skipped (all lines filtered), False otherwise
    """
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

    filtered_line_changes = _filter_consumed_replacement_masks(filtered_line_changes)
    if filtered_line_changes is None:
        return True

    # Update cached hunk with filtered version
    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(convert_line_changes_to_serializable_dict(filtered_line_changes),
                  ensure_ascii=False, indent=0)
    )

    return False


def _filter_consumed_replacement_masks(
    line_changes: LineLevelChange,
) -> LineLevelChange | None:
    """Hide synthetic replacement runs created by `include --line --as`."""
    file_metadata = read_consumed_file_metadata(line_changes.path)
    replacement_masks = file_metadata.get("replacement_masks", []) if file_metadata else []
    if not replacement_masks:
        return line_changes

    normalized_masks: set[tuple[tuple[str, str], ...]] = set()
    for mask in replacement_masks:
        deleted_signature = tuple(("-", text) for text in mask.get("deleted_lines", []))
        added_signature = tuple(("+", text) for text in mask.get("added_lines", []))
        full_signature = deleted_signature + added_signature
        if full_signature:
            normalized_masks.add(full_signature)
        if deleted_signature:
            normalized_masks.add(deleted_signature)
        if added_signature:
            normalized_masks.add(added_signature)

    filtered_lines = []
    changed_run: list[LineEntry] = []

    def flush_changed_run() -> None:
        nonlocal changed_run
        if not changed_run:
            return
        run_signature = tuple((line.kind, line.text) for line in changed_run if line.kind in ("+", "-"))
        if run_signature not in normalized_masks:
            filtered_lines.extend(changed_run)
        changed_run = []

    for line_entry in line_changes.lines:
        if line_entry.kind in ("+", "-"):
            changed_run.append(line_entry)
            continue
        flush_changed_run()
        filtered_lines.append(line_entry)

    flush_changed_run()

    new_id = 1
    renumbered_lines = []
    for line_entry in filtered_lines:
        renumbered_lines.append(
            replace(line_entry, id=new_id if line_entry.kind != " " else None)
        )
        if line_entry.kind != " ":
            new_id += 1

    has_changes_after_filter = any(line.kind in ("+", "-") for line in renumbered_lines)
    if not has_changes_after_filter:
        return None

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=renumbered_lines,
    )


def render_batch_file_display(
    batch_name: str,
    file_path: str,
    metadata: dict | None = None,
) -> Optional['RenderedBatchDisplay']:
    """Pure function to render batch file display with gutter ID translation.

    This is a side-effect-free helper that:
    - Reads batch metadata
    - Reads batch source content
    - Reads current working tree content
    - Probes individual line mergeability
    - Builds LineLevelChange with original selection IDs
    - Builds gutter ID mappings

    It does not:
    - Write cache files
    - Mutate selected hunk state
    - Compute patch hashes

    Args:
        batch_name: Name of the batch
        file_path: Specific file to render

    Returns:
        RenderedBatchDisplay with line changes and gutter ID translation, or None if file not found.
    """
    # Read batch metadata
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
    display_lines = batch_display.build_display_lines_from_batch_source(
        batch_source_content_str,
        ownership,
        context_lines=get_context_lines(),
    )

    if not display_lines:
        return None

    # Determine which display lines should get gutter IDs
    # Include:
    # 1. Individually mergeable lines (can be selected alone)
    # 2. Lines that are part of atomic units (can be selected together with unit)
    mergeable_ids = set()

    # Build ownership units to identify atomic groupings
    units = build_ownership_units_from_display_lines(ownership, display_lines)

    # Check each ownership unit once.  All lines in an atomic unit share the
    # same mergeability result, and merge_batch performs the expensive
    # structural matching internally.
    for unit in units:
        try:
            validate_ownership_units([unit])
            ownership_for_unit = rebuild_ownership_from_units([unit])
            if ownership_for_unit.is_empty():
                continue
            batch_merge.merge_batch(batch_source_content_bytes, ownership_for_unit, working_content)
            mergeable_ids.update(unit.display_line_ids)
        except (MergeError, ValueError, KeyError, Exception):
            # Unit not mergeable - exclude all its lines
            pass

    # Keep original selection IDs; mergeability is stored separately.
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
        elif display_line["type"] == "context":
            line_entries.append(LineEntry(
                id=None,
                kind=" ",
                old_line_number=None,
                new_line_number=new_line_num,
                text_bytes=text_bytes,
                text=text,
                source_line=display_line["source_line"]
            ))
            new_line_num += 1
        elif display_line["type"] == "gap":
            line_entries.append(LineEntry(
                id=None,
                kind=" ",
                old_line_number=None,
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
    # Only mergeable lines get consecutive gutter IDs (1, 2, 3...)
    gutter_to_selection_id = {}
    selection_id_to_gutter = {}
    gutter_num = 1
    for entry in line_entries:
        if entry.id is not None and entry.id in mergeable_ids:
            gutter_to_selection_id[gutter_num] = entry.id
            selection_id_to_gutter[entry.id] = gutter_num
            gutter_num += 1

    return RenderedBatchDisplay(
        line_changes=line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
        selection_id_to_gutter=selection_id_to_gutter
    )


def cache_batch_as_single_hunk(
    batch_name: str,
    file_path: str | None = None,
    metadata: dict | None = None,
) -> Optional['RenderedBatchDisplay']:
    """Load file from batch and cache it as a single hunk using batch source model.

    Args:
        batch_name: Name of the batch to load
        file_path: Specific file to cache, or None for first file

    Returns:
        RenderedBatchDisplay with line changes and gutter ID translation, or None if batch is empty or file not found.
        The gutter_to_selection_id mapping translates user-visible filtered gutter IDs (1, 2, 3...)
        to original selection IDs for ownership selection commands.
    """
    # Read batch metadata
    if metadata is None:
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
    rendered = render_batch_file_display(batch_name, file_path, metadata=metadata)
    if rendered is None:
        return None

    cache_rendered_batch_file_display(file_path, rendered)
    return rendered


def cache_rendered_batch_file_display(
    file_path: str,
    rendered: 'RenderedBatchDisplay',
) -> None:
    """Cache an already rendered batch file as the selected hunk."""
    line_changes = rendered.line_changes
    line_entries = line_changes.lines
    header = line_changes.header

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

    # Cache the hunk bytes exactly; display strings are derived elsewhere.
    write_file_bytes(get_selected_hunk_patch_file_path(), patch_bytes)
    write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
    write_selected_change_kind(SelectedChangeKind.BATCH_FILE)

    # Save LineLevelChange for line-level operations
    write_text_file_contents(get_line_changes_json_file_path(),
                            json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                      ensure_ascii=False, indent=0))

    # No snapshots for batch hunks (they don't track staleness)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)


def cache_batch_files_generator(
    batch_name: str,
    metadata: dict | None = None,
) -> Generator['RenderedBatchDisplay', None, None]:
    """Yield RenderedBatchDisplay for each file in batch.

    Files are yielded in sorted order. Each file has line IDs
    from original display IDs. Batch content comes from batch storage (not working tree).

    Args:
        batch_name: Name of the batch

    Yields:
        RenderedBatchDisplay for each file in batch with gutter ID translation.
    """
    # Read batch metadata
    if metadata is None:
        metadata = read_batch_metadata(batch_name)
    files = sorted(metadata.get("files", {}).keys())

    for file_path in files:
        # Use pure render helper (side-effect free)
        rendered = render_batch_file_display(batch_name, file_path, metadata=metadata)
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
    try:
        combined_line_changes = render_file_as_single_hunk(file_path)
        return _cache_combined_file_line_changes(file_path, combined_line_changes)
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes in file)
        return None


def cache_unstaged_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Cache the remaining unstaged changes for a file as a single hunk."""
    try:
        combined_line_changes = render_unstaged_file_as_single_hunk(file_path)
        return _cache_combined_file_line_changes(file_path, combined_line_changes)
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes in file)
        return None


def _cache_combined_file_line_changes(
    file_path: str,
    combined_line_changes: Optional[LineLevelChange],
) -> Optional[LineLevelChange]:
    """Persist a combined file-scoped view as the current selection."""
    if combined_line_changes is None:
        return None

    # Synthesize patch bytes for caching (used for hashing/identity)
    patch_lines = [
        f"--- a/{file_path}\n".encode("utf-8"),
        f"+++ b/{file_path}\n".encode("utf-8"),
        (
            f"@@ -{combined_line_changes.header.old_start},{combined_line_changes.header.old_len} "
            f"+{combined_line_changes.header.new_start},{combined_line_changes.header.new_len} @@\n"
        ).encode("utf-8"),
    ]
    for entry in combined_line_changes.lines:
        patch_lines.append(entry.kind.encode("utf-8") + entry.text_bytes + b"\n")
    patch_bytes = b"".join(patch_lines)

    patch_hash = compute_stable_hunk_hash(patch_bytes)

    # Cache the combined hunk
    write_file_bytes(get_selected_hunk_patch_file_path(), patch_bytes)
    write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
    write_selected_change_kind(SelectedChangeKind.FILE)
    write_text_file_contents(get_line_changes_json_file_path(),
                            json.dumps(convert_line_changes_to_serializable_dict(combined_line_changes),
                                      ensure_ascii=False, indent=0))

    # Cache live snapshots so later line-level operations can reuse the
    # file-scoped selection the same way they reuse ordinary selected hunks.
    write_snapshots_for_selected_file_path(file_path)

    return combined_line_changes


def render_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Render all changes for a file as a single hunk without caching state."""
    return _build_combined_file_line_changes(
        file_path,
        parse_unified_diff_streaming(
            stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color", "HEAD", "--", file_path])
        ),
    )


def render_unstaged_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Render the remaining unstaged changes for a file as a single hunk."""
    return _build_combined_file_line_changes(
        file_path,
        parse_unified_diff_streaming(
            stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color", "--", file_path])
        ),
    )


def build_file_hunk_from_content(file_path: str, file_content: bytes) -> Optional[LineLevelChange]:
    """Build a file-scoped line view for hypothetical file content without writing it."""
    head_result = run_git_command(["show", f"HEAD:{file_path}"], check=False, text_output=False)
    head_content = head_result.stdout if head_result.returncode == 0 else b""

    with tempfile.NamedTemporaryFile(delete=False) as old_tmp:
        old_tmp.write(head_content)
        old_path = old_tmp.name
    with tempfile.NamedTemporaryFile(delete=False) as new_tmp:
        new_tmp.write(file_content)
        new_path = new_tmp.name

    try:
        diff_result = run_git_command(
            [
                "diff",
                "--no-index",
                f"-U{get_context_lines()}",
                "--no-color",
                old_path,
                new_path,
            ],
            check=False,
            text_output=False,
        )
        if diff_result.returncode not in (0, 1):
            raise subprocess.CalledProcessError(
                diff_result.returncode,
                diff_result.args,
                output=diff_result.stdout,
                stderr=diff_result.stderr,
            )

        patch_bytes = diff_result.stdout.replace(
            f"a{old_path}".encode("utf-8"),
            f"a/{file_path}".encode("utf-8"),
        ).replace(
            f"b{new_path}".encode("utf-8"),
            f"b/{file_path}".encode("utf-8"),
        )

        return _build_combined_file_line_changes(
            file_path,
            parse_unified_diff_streaming(patch_bytes.splitlines(keepends=True)),
        )
    finally:
        try:
            import os
            os.unlink(old_path)
            os.unlink(new_path)
        except OSError:
            pass


def _build_combined_file_line_changes(
    file_path: str,
    patches,
) -> Optional[LineLevelChange]:
    """Combine file diff hunks into one file-scoped LineLevelChange."""
    all_line_entries = []
    line_id_counter = 1
    min_old_start = None
    max_old_end = None
    min_new_start = None
    max_new_end = None
    previous_old_end = None
    previous_new_end = None

    for single_hunk in patches:
        patch_bytes = single_hunk.to_patch_bytes()
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)

        if previous_old_end is not None and previous_new_end is not None:
            omitted_old_lines = line_changes.header.old_start - previous_old_end
            omitted_new_lines = line_changes.header.new_start - previous_new_end
            omitted_line_count = max(omitted_old_lines, omitted_new_lines)
            if omitted_line_count > 0:
                gap_text = ngettext(
                    "... {count} more line ...",
                    "... {count} more lines ...",
                    omitted_line_count,
                ).format(count=omitted_line_count)
                all_line_entries.append(
                    LineEntry(
                        id=None,
                        kind=" ",
                        old_line_number=None,
                        new_line_number=None,
                        text_bytes=gap_text.encode("utf-8"),
                        text=gap_text,
                        source_line=None,
                    )
                )

        if min_old_start is None:
            min_old_start = line_changes.header.old_start
            min_new_start = line_changes.header.new_start

        max_old_end = line_changes.header.old_start + line_changes.header.old_len
        max_new_end = line_changes.header.new_start + line_changes.header.new_len
        previous_old_end = max_old_end
        previous_new_end = max_new_end

        for line_entry in line_changes.lines:
            if line_entry.kind != " ":
                new_entry = LineEntry(
                    id=line_id_counter,
                    kind=line_entry.kind,
                    old_line_number=line_entry.old_line_number,
                    new_line_number=line_entry.new_line_number,
                    text_bytes=line_entry.text_bytes,
                    text=line_entry.text,
                    source_line=line_entry.source_line,
                )
                line_id_counter += 1
            else:
                new_entry = LineEntry(
                    id=None,
                    kind=line_entry.kind,
                    old_line_number=line_entry.old_line_number,
                    new_line_number=line_entry.new_line_number,
                    text_bytes=line_entry.text_bytes,
                    text=line_entry.text,
                    source_line=line_entry.source_line,
                )
            all_line_entries.append(new_entry)

    if not all_line_entries:
        return None

    combined_header = HunkHeader(
        old_start=min_old_start,
        old_len=max_old_end - min_old_start,
        new_start=min_new_start,
        new_len=max_new_end - min_new_start,
    )

    return LineLevelChange(
        path=file_path,
        header=combined_header,
        lines=all_line_entries,
    )


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
                write_selected_change_kind(SelectedChangeKind.BINARY)

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

            write_file_bytes(get_selected_hunk_patch_file_path(), patch_bytes)
            write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)
            write_selected_change_kind(SelectedChangeKind.HUNK)

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
        patch_bytes = read_file_bytes(patch_path)
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
        patch_bytes = read_file_bytes(patch_path)
        line_changes = build_line_changes_from_patch_bytes(patch_bytes)
        print_line_level_changes(line_changes)
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

    # Read cached snapshots as bytes so non-UTF-8 content compares exactly.
    cached_index_content = read_file_bytes(snapshot_base_path)
    cached_worktree_content = read_file_bytes(snapshot_new_path)

    # Get selected file content from index
    try:
        result = run_git_command(["show", f":{file_path}"], check=False, text_output=False)
        if result.returncode != 0:
            # File not in index (was deleted, or never added)
            selected_index_content = b""
        else:
            selected_index_content = result.stdout
    except Exception:
        return True  # Error reading means state is stale

    # Get selected file content from working tree
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    try:
        selected_worktree_content = read_file_bytes(file_full_path)
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
    selected_kind = read_selected_change_kind()

    # Clear processed IDs since old line numbers don't apply to fresh hunk
    write_line_ids_file(get_processed_include_ids_file_path(), set())
    write_line_ids_file(get_processed_skip_ids_file_path(), set())

    if selected_kind == SelectedChangeKind.FILE:
        line_changes = cache_unstaged_file_as_single_hunk(file_path)
        if line_changes is None:
            clear_selected_change_state_files()
            from ..commands.show import command_show
            command_show()
            return

        print_line_level_changes(line_changes)
        return

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

            write_file_bytes(get_selected_hunk_patch_file_path(), patch_bytes)
            write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)
            write_selected_change_kind(SelectedChangeKind.HUNK)

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


def record_binary_hunk_skipped(binary_change: BinaryFileChange, hunk_hash: str) -> None:
    """Record that a binary change was skipped with file-level metadata."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    metadata = {
        "hash": hunk_hash,
        "file": file_path,
        "line": None,
        "ids": [],
        "change_type": binary_change.change_type,
    }

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
