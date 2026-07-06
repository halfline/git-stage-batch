"""Hunk navigation, state management, staleness detection, and progress tracking."""

from __future__ import annotations

import json
import tempfile
import subprocess
import sys
from enum import Enum
from hashlib import sha256
from typing import Generator, Mapping, Optional, Union

from ..batch.attribution import build_file_attribution, filter_owned_diff_fragments
from ..batch.display import annotate_with_batch_source
from ..batch.query import list_batch_names, read_batch_metadata
from ..core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ..core.models import (
    BinaryFileChange,
    GitlinkChange,
    LineLevelChange,
    HunkHeader,
    LineEntry,
    RenameChange,
    RenderedBatchDisplay,
    TextFileDeletionChange,
)
from ..core.text_lifecycle import detect_empty_text_lifecycle_change
from ..core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
)
from ..editor import (
    EditorBuffer,
    load_git_object_as_buffer,
)
from ..core.line_selection import write_line_ids_file
from ..core.line_identity import preserve_line_ids_from_previous_view
from ..exceptions import CommandError, NoMoreHunks, exit_with_error
from ..i18n import _, ngettext
from ..output import (
    print_line_level_changes,
    print_binary_file_change,
    print_gitlink_change,
    print_rename_change,
    print_text_file_deletion_change,
)
from .consumed_selections import read_consumed_file_metadata
from .auto_advance import resolve_auto_advance
from ..batch.file_display import render_batch_file_display as render_batch_file_display
from .file_tracking import auto_add_untracked_files
from .progress import (
    format_id_range as format_id_range,
    record_binary_hunk_skipped as record_binary_hunk_skipped,
    record_gitlink_hunk_skipped as record_gitlink_hunk_skipped,
    record_hunk_discarded as record_hunk_discarded,
    record_hunk_included as record_hunk_included,
    record_hunk_skipped as record_hunk_skipped,
    record_hunks_discarded as record_hunks_discarded,
    record_rename_hunk_skipped as record_rename_hunk_skipped,
    record_text_deletion_hunk_skipped as record_text_deletion_hunk_skipped,
)
from .selected_change.snapshots import (
    snapshots_are_stale as snapshots_are_stale,
    write_snapshots_for_selected_file_path,
)
from ..utils.file_io import (
    is_path_blocked,
    read_file_paths_file,
    read_text_file_line_set,
    read_text_file_contents,
    write_text_file_contents,
)
from ..utils.git import (
    stream_git_command,
    stream_git_diff,
)
from ..utils.text import bytes_to_lines
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from .batch_selected_changes import (
    compute_batch_binary_fingerprint as compute_batch_binary_fingerprint,
    require_current_selected_batch_binary_file_for_batch as require_current_selected_batch_binary_file_for_batch,
    selected_batch_binary_batch_name as selected_batch_binary_batch_name,
    selected_batch_binary_file_for_batch as selected_batch_binary_file_for_batch,
    selected_batch_binary_matches_batch as selected_batch_binary_matches_batch,
)
from .selected_change.lifecycle import clear_selected_change_state_files as clear_selected_change_state_files
from .selected_change.store import (
    SelectedChangeClearReason as SelectedChangeClearReason,
    SelectedChangeKind,
    SelectedChangeStateSnapshot as SelectedChangeStateSnapshot,
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rename_change,
    cache_text_deletion_change,
    get_selected_change_file_path as get_selected_change_file_path,
    load_line_changes_from_patch_path as _load_line_changes_from_patch_path,
    load_selected_binary_file,
    load_selected_gitlink_change,
    load_selected_rename_change,
    load_selected_text_deletion_change,
    mark_selected_change_cleared_by_auto_advance_disabled,
    mark_selected_change_cleared_by_file_list as mark_selected_change_cleared_by_file_list,
    mark_selected_change_cleared_by_stale_batch_selection,
    read_selected_change_kind,
    read_selected_gitlink_data as _read_selected_gitlink_data,
    refuse_bare_action_after_auto_advance_disabled as refuse_bare_action_after_auto_advance_disabled,
    refuse_bare_action_after_file_list as refuse_bare_action_after_file_list,
    refuse_bare_action_after_stale_batch_selection as refuse_bare_action_after_stale_batch_selection,
    restore_selected_change_state as restore_selected_change_state,
    selected_change_was_cleared_by_auto_advance_disabled as selected_change_was_cleared_by_auto_advance_disabled,
    selected_change_was_cleared_by_file_list as selected_change_was_cleared_by_file_list,
    selected_change_was_cleared_by_stale_batch_selection as selected_change_was_cleared_by_stale_batch_selection,
    snapshot_selected_change_state as snapshot_selected_change_state,
    write_line_changes_state as _write_line_changes_state,
    write_selected_change_kind,
    write_selected_hunk_patch_lines,
)


class RecalculateSelectedHunkResult(str, Enum):
    """Outcome from refreshing the selected hunk for one file."""

    RECALCULATED = "recalculated"
    CLEARED = "cleared"
    SHOW_NEXT_CHANGE = "show-next-change"


_BATCH_MERGE_REVIEW_ACTIONS = (
    "include-from-batch",
    "discard-from-batch",
    "apply-from-batch",
)
_BATCH_RESET_REVIEW_ACTION = "reset-from-batch"


def stream_live_git_diff(**kwargs):
    """Stream actionable live changes with rename detection enabled."""
    kwargs.setdefault("find_renames", True)
    return stream_git_diff(**kwargs)


def compute_batch_gitlink_fingerprint(
    file_path: str,
    file_meta: Mapping[str, object],
) -> str:
    """Return a stable identity for the current stored submodule pointer."""
    payload = {
        "file_path": file_path,
        "file_type": file_meta.get("file_type"),
        "change_type": file_meta.get("change_type"),
        "mode": file_meta.get("mode"),
        "old_oid": file_meta.get("old_oid"),
        "new_oid": file_meta.get("new_oid"),
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8", errors="surrogateescape")).hexdigest()


def selected_batch_gitlink_matches_batch(batch_name: str) -> bool:
    """Return whether the cached batch submodule pointer came from this batch."""
    if read_selected_change_kind() != SelectedChangeKind.BATCH_GITLINK:
        return False
    gitlink_data = _read_selected_gitlink_data()
    if gitlink_data is None:
        return False
    return gitlink_data.get("batch_name") == batch_name


def selected_batch_gitlink_file_for_batch(
    batch_name: str,
    all_files: Mapping[str, dict],
) -> str | None:
    """Return the selected batch submodule pointer if it is still current."""
    if not selected_batch_gitlink_matches_batch(batch_name):
        return None

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is None:
        return None

    file_path = gitlink_change.path()
    file_meta = all_files.get(file_path)
    if file_meta is None:
        return None
    if file_meta.get("file_type") != "gitlink":
        return None
    if file_meta.get("change_type") != gitlink_change.change_type:
        return None
    if file_meta.get("old_oid") != gitlink_change.old_oid:
        return None
    if file_meta.get("new_oid") != gitlink_change.new_oid:
        return None

    gitlink_data = _read_selected_gitlink_data()
    if gitlink_data is None:
        return None
    cached_fingerprint = gitlink_data.get("batch_gitlink_fingerprint")
    if not isinstance(cached_fingerprint, str):
        return None
    current_fingerprint = compute_batch_gitlink_fingerprint(file_path, file_meta)
    if current_fingerprint != cached_fingerprint:
        return None

    return file_path


def require_current_selected_batch_gitlink_file_for_batch(
    batch_name: str,
    all_files: Mapping[str, dict],
) -> str | None:
    """Return selected batch submodule pointer, or refuse if it went stale."""
    if not selected_batch_gitlink_matches_batch(batch_name):
        return None

    selected_file = selected_batch_gitlink_file_for_batch(batch_name, all_files)
    if selected_file is not None:
        return selected_file

    gitlink_change = load_selected_gitlink_change()
    file_path = gitlink_change.path() if gitlink_change is not None else "the selected batch submodule pointer"
    clear_selected_change_state_files()
    mark_selected_change_cleared_by_stale_batch_selection(
        batch_name=batch_name,
        file_path=file_path,
    )
    exit_with_error(
        _(
            "The selected batch submodule pointer no longer matches batch '{name}'.\n"
            "Show the batch again before using a pathless batch action."
        ).format(name=batch_name)
    )


def binary_file_change_is_stale(binary_change: BinaryFileChange) -> bool:
    """Return whether a cached binary selection no longer matches repository state."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    if snapshots_are_stale(file_path):
        return True
    current_change = render_binary_file_change(file_path)
    if current_change is None:
        return True
    return (
        current_change.old_path != binary_change.old_path
        or current_change.new_path != binary_change.new_path
        or current_change.change_type != binary_change.change_type
    )


def gitlink_change_is_stale(gitlink_change: GitlinkChange) -> bool:
    """Return whether a cached gitlink selection no longer matches Git state."""
    current_change = render_gitlink_change(gitlink_change.path())
    if current_change is None:
        return True
    return (
        current_change.old_path != gitlink_change.old_path
        or current_change.new_path != gitlink_change.new_path
        or current_change.old_oid != gitlink_change.old_oid
        or current_change.new_oid != gitlink_change.new_oid
        or current_change.change_type != gitlink_change.change_type
    )


def rename_change_is_stale(rename_change: RenameChange) -> bool:
    """Return whether a cached rename selection no longer matches Git state."""
    current_change = render_rename_change(rename_change.new_path)
    if current_change is None:
        current_change = render_rename_change(rename_change.old_path)
    if current_change is None:
        return True
    return (
        current_change.old_path != rename_change.old_path
        or current_change.new_path != rename_change.new_path
    )


def text_deletion_change_is_stale(deletion_change: TextFileDeletionChange) -> bool:
    """Return whether a cached text deletion selection no longer matches Git state."""
    if snapshots_are_stale(deletion_change.path()):
        return True
    current_change = render_text_deletion_change(deletion_change.path())
    if current_change is None:
        return True
    return (
        current_change.old_path != deletion_change.old_path
        or current_change.new_path != deletion_change.new_path
    )


def load_selected_change() -> Optional[Union[LineLevelChange, BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange]]:
    """Load the currently cached selected change, if any."""
    selected_kind = read_selected_change_kind()
    rename_change = load_selected_rename_change()
    if rename_change is not None:
        if selected_kind == SelectedChangeKind.RENAME and rename_change_is_stale(rename_change):
            raise CommandError(
                _(
                    "Selected rename no longer matches the working tree: {old} -> {new}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(old=rename_change.old_path, new=rename_change.new_path)
            )
        return rename_change

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        if selected_kind == SelectedChangeKind.DELETION and text_deletion_change_is_stale(deletion_change):
            raise CommandError(
                _(
                    "Selected text file deletion no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=deletion_change.path())
            )
        return deletion_change

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        if selected_kind == SelectedChangeKind.GITLINK and gitlink_change_is_stale(gitlink_change):
            raise CommandError(
                _(
                    "Selected submodule pointer no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=gitlink_change.path())
            )
        return gitlink_change

    binary_file = load_selected_binary_file()
    if binary_file is not None:
        if selected_kind == SelectedChangeKind.BINARY and binary_file_change_is_stale(binary_file):
            file_path = binary_file.new_path if binary_file.new_path != "/dev/null" else binary_file.old_path
            raise CommandError(
                _(
                    "Selected binary file no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=file_path)
            )
        return binary_file

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    require_selected_hunk()

    line_changes = load_line_changes_from_state()
    if line_changes is not None:
        return line_changes

    return _load_line_changes_from_patch_path(patch_path)


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

    if not line_changes.lines and _empty_text_lifecycle_change_is_batched(file_path):
        return True

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


def _empty_text_lifecycle_change_is_batched(file_path: str) -> bool:
    """Return whether the current empty text lifecycle diff is already batched."""
    change_type = detect_empty_text_lifecycle_change(file_path)
    if change_type is None:
        return False

    for batch_name in list_batch_names():
        file_meta = read_batch_metadata(batch_name).get("files", {}).get(file_path)
        if file_meta is not None and file_meta.get("change_type") == change_type:
            return True
    return False


def text_deletion_change_is_batched(deletion_change: TextFileDeletionChange) -> bool:
    """Return whether a whole-text-file deletion is already represented in a batch."""
    return _empty_text_lifecycle_change_is_batched(deletion_change.path())


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
        run_signature = tuple(
            (line.kind, line.display_text())
            for line in changed_run
            if line.kind in ("+", "-")
        )
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

    has_changes_after_filter = any(line.kind in ("+", "-") for line in filtered_lines)
    if not has_changes_after_filter:
        return None

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=filtered_lines,
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

    patch_lines = [
        f"--- {old_path}\n".encode('utf-8'),
        f"+++ {new_path}\n".encode('utf-8'),
        f"@@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@\n".encode('utf-8')
    ]
    for entry in line_entries:
        patch_lines.append(entry.kind.encode('utf-8') + entry.text_bytes + b'\n')
        if not entry.has_trailing_newline:
            patch_lines.append(b"\\ No newline at end of file\n")

    patch_hash = compute_stable_hunk_hash_from_lines(patch_lines)

    # Cache the hunk bytes exactly; display strings are derived elsewhere.
    write_selected_hunk_patch_lines(patch_lines)
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
        if not entry.has_trailing_newline:
            patch_lines.append(b"\\ No newline at end of file\n")

    patch_hash = compute_stable_hunk_hash_from_lines(patch_lines)

    # Cache the combined hunk
    write_selected_hunk_patch_lines(patch_lines)
    write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
    write_selected_change_kind(SelectedChangeKind.FILE)
    write_line_ids_file(get_processed_include_ids_file_path(), set())
    write_line_ids_file(get_processed_skip_ids_file_path(), set())
    write_text_file_contents(get_line_changes_json_file_path(),
                            json.dumps(convert_line_changes_to_serializable_dict(combined_line_changes),
                                      ensure_ascii=False, indent=0))

    # Cache live snapshots so later line-level operations can reuse the
    # file-scoped selection the same way they reuse ordinary selected hunks.
    write_snapshots_for_selected_file_path(file_path)

    return combined_line_changes


def render_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Render all changes for a file as a single hunk without caching state."""
    auto_add_untracked_files([file_path])
    with acquire_unified_diff(
        stream_live_git_diff(
            base="HEAD",
            context_lines=get_context_lines(),
            full_index=True,
            ignore_submodules="none",
            submodule_format="short",
            paths=[file_path],
        )
    ) as patches:
        return _build_combined_file_line_changes(
            file_path,
            patches,
        )


def render_binary_file_change(file_path: str) -> Optional[BinaryFileChange]:
    """Render a binary file change for file-scoped display without caching state."""
    auto_add_untracked_files([file_path])
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                base="HEAD",
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
                paths=[file_path],
            )
        ) as patches:
            for item in patches:
                if isinstance(item, BinaryFileChange):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None


def render_gitlink_change(file_path: str) -> Optional[GitlinkChange]:
    """Render a gitlink change for file-scoped display without caching state."""
    auto_add_untracked_files([file_path])
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                base="HEAD",
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
                paths=[file_path],
            )
        ) as patches:
            for item in patches:
                if isinstance(item, GitlinkChange):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None


def render_rename_change(file_path: str) -> Optional[RenameChange]:
    """Render a rename change involving file_path without caching state."""
    auto_add_untracked_files([file_path])
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for item in patches:
                if (
                    isinstance(item, RenameChange)
                    and file_path in (item.old_path, item.new_path)
                ):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None


def render_text_deletion_change(file_path: str) -> Optional[TextFileDeletionChange]:
    """Render a whole-text-file deletion for file-scoped display without caching state."""
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
                paths=[file_path],
            )
        ) as patches:
            for item in patches:
                if isinstance(item, TextFileDeletionChange):
                    return item
    except subprocess.CalledProcessError:
        return None
    return None


def render_unstaged_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Render the remaining unstaged changes for a file as a single hunk."""
    auto_add_untracked_files([file_path])
    with acquire_unified_diff(
        stream_live_git_diff(
            context_lines=get_context_lines(),
            full_index=True,
            ignore_submodules="none",
            submodule_format="short",
            paths=[file_path],
        )
    ) as patches:
        return _build_combined_file_line_changes(
            file_path,
            patches,
        )


def build_file_hunk_from_buffer(
    file_path: str,
    file_buffer: EditorBuffer,
) -> Optional[LineLevelChange]:
    """Build a file-scoped line view for a hypothetical file buffer without writing it."""
    head_buffer = load_git_object_as_buffer(f"HEAD:{file_path}")

    with tempfile.NamedTemporaryFile(delete=False) as old_tmp:
        if head_buffer is not None:
            with head_buffer:
                for chunk in head_buffer.byte_chunks():
                    old_tmp.write(chunk)
        old_path = old_tmp.name
    with tempfile.NamedTemporaryFile(delete=False) as new_tmp:
        for chunk in file_buffer.byte_chunks():
            new_tmp.write(chunk)
        new_path = new_tmp.name

    try:
        with acquire_unified_diff(
            _stream_no_index_diff_lines(
                file_path=file_path,
                old_path=old_path,
                new_path=new_path,
            )
        ) as patches:
            return _build_combined_file_line_changes(
                file_path,
                patches,
            )
    finally:
        try:
            import os
            os.unlink(old_path)
            os.unlink(new_path)
        except OSError:
            pass


def _stream_no_index_diff_lines(
    *,
    file_path: str,
    old_path: str,
    new_path: str,
) -> Generator[bytes, None, None]:
    arguments = [
        "diff",
        "--no-index",
        f"-U{get_context_lines()}",
        "--no-color",
        old_path,
        new_path,
    ]
    stderr_chunks: list[bytes] = []
    exit_code = 0

    def stdout_chunks() -> Generator[bytes, None, None]:
        nonlocal exit_code

        try:
            yield from stream_git_command(arguments, requires_index_lock=False)
        except subprocess.CalledProcessError as error:
            exit_code = error.returncode
            stderr = error.stderr
            if stderr is not None:
                stderr_chunks.append(stderr.encode("utf-8", errors="replace"))

    old_header = f"a{old_path}".encode("utf-8")
    new_header = f"b{new_path}".encode("utf-8")
    rendered_old_header = f"a/{file_path}".encode("utf-8")
    rendered_new_header = f"b/{file_path}".encode("utf-8")

    for line in bytes_to_lines(stdout_chunks()):
        yield line.replace(old_header, rendered_old_header).replace(
            new_header,
            rendered_new_header,
        )

    if exit_code not in (0, 1):
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise subprocess.CalledProcessError(
            exit_code,
            arguments,
            stderr=stderr_text,
        )


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
        if isinstance(single_hunk, (BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange)):
            continue

        line_changes = build_line_changes_from_patch_lines(single_hunk.lines)

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
                    source_line=line_entry.source_line,
                    has_trailing_newline=line_entry.has_trailing_newline,
                )
                line_id_counter += 1
            else:
                new_entry = LineEntry(
                    id=None,
                    kind=line_entry.kind,
                    old_line_number=line_entry.old_line_number,
                    new_line_number=line_entry.new_line_number,
                    text_bytes=line_entry.text_bytes,
                    source_line=line_entry.source_line,
                    has_trailing_newline=line_entry.has_trailing_newline,
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


def fetch_next_change() -> Union[LineLevelChange, BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange]:
    """Find the next hunk or binary file that isn't blocked and cache it as selected.

    Returns:
        LineLevelChange for text hunks, BinaryFileChange for binary files.

    Raises:
        NoMoreHunks: When there are no more items to process.
    """
    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist (includes selected iteration)
    blocked_hashes = read_text_file_line_set(get_block_list_file_path())

    # Stream git diff and parse incrementally - stops after first unblocked item found
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for item in patches:
                if isinstance(item, RenameChange):
                    rename_hash = compute_rename_change_hash(item)
                    if rename_hash in blocked_hashes:
                        continue

                    if (
                        is_path_blocked(item.old_path, blocked_files)
                        or is_path_blocked(item.new_path, blocked_files)
                    ):
                        continue

                    cache_rename_change(item)
                    return item

                if isinstance(item, TextFileDeletionChange):
                    deletion_hash = compute_text_file_deletion_hash(item)
                    if deletion_hash in blocked_hashes or text_deletion_change_is_batched(item):
                        continue

                    if is_path_blocked(item.path(), blocked_files):
                        continue

                    cache_text_deletion_change(item)
                    return item

                if isinstance(item, GitlinkChange):
                    gitlink_hash = compute_gitlink_change_hash(item)
                    if gitlink_hash in blocked_hashes:
                        continue

                    if is_path_blocked(item.path(), blocked_files):
                        continue

                    cache_gitlink_change(item)
                    return item

                # Handle binary files
                if isinstance(item, BinaryFileChange):
                    binary_hash = compute_binary_file_hash(item)
                    if binary_hash in blocked_hashes:
                        continue

                    # Determine file path for blocked files check
                    file_path = item.new_path if item.new_path != "/dev/null" else item.old_path
                    if is_path_blocked(file_path, blocked_files):
                        continue

                    cache_binary_file_change(item)

                    # Return the BinaryFileChange object directly
                    return item

                # Handle text hunks (SingleHunkPatch)
                if item.old_path != item.new_path:
                    rename_hash = compute_rename_change_hash(
                        RenameChange(old_path=item.old_path, new_path=item.new_path)
                    )
                    if rename_hash in blocked_hashes:
                        continue

                hunk_hash = compute_stable_hunk_hash_from_lines(item.lines)
                if hunk_hash in blocked_hashes:
                    continue

                # Skip hunks from blocked files
                line_changes = build_line_changes_from_patch_lines(
                    item.lines,
                    annotator=annotate_with_batch_source,
                )
                if is_path_blocked(line_changes.path, blocked_files):
                    continue

                write_selected_hunk_patch_lines(item.lines)
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
    rename_change = load_selected_rename_change()
    if rename_change is not None:
        print_rename_change(rename_change)
        return

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        print_text_file_deletion_change(deletion_change)
        return

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        print_gitlink_change(gitlink_change)
        return

    # Check if selected item is a binary file
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        print_binary_file_change(binary_file)
        return

    # Otherwise, show text hunk
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        line_changes = _load_line_changes_from_patch_path(patch_path)
        print_line_level_changes(line_changes)


def advance_to_and_show_next_change() -> None:
    """Advance to next hunk/binary file and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk/binary file with displaying it. If no more items exist,
    prints a message to stderr.
    """
    advance_to_next_change()

    rename_change = load_selected_rename_change()
    if rename_change is not None:
        print_rename_change(rename_change)
        return

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        print_text_file_deletion_change(deletion_change)
        return

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        print_gitlink_change(gitlink_change)
        return

    # Check if a binary file was cached
    binary_file = load_selected_binary_file()
    if binary_file is not None:
        print_binary_file_change(binary_file)
        return

    # Check if a text hunk was cached
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        line_changes = _load_line_changes_from_patch_path(patch_path)
        print_line_level_changes(line_changes)
    else:
        print(_("No more hunks to process."), file=sys.stderr)


def finish_selected_change_action(
    *,
    quiet: bool,
    auto_advance: bool | None = None,
) -> None:
    """Apply the configured selection step after a hunk action completes."""
    if not select_next_change_after_action(auto_advance=auto_advance):
        return

    if quiet:
        return

    if read_selected_change_kind() is None:
        print(_("No more hunks to process."), file=sys.stderr)
        return

    show_selected_change()


def select_next_change_after_action(
    *,
    auto_advance: bool | None = None,
) -> bool:
    """Select the next hunk after an action, or leave selection empty."""
    if resolve_auto_advance(auto_advance):
        advance_to_next_change()
        return True

    clear_selected_change_state_files()
    mark_selected_change_cleared_by_auto_advance_disabled()
    return False




def require_selected_hunk() -> None:
    """Ensure selected hunk exists and is not stale, exit with error otherwise."""
    if read_selected_change_kind() in (SelectedChangeKind.BATCH_FILE, SelectedChangeKind.BATCH_BINARY):
        exit_with_error(
            _(
                "Selected file came from a batch, not a live hunk. "
                "Open a live hunk with 'show' or use the matching '--from' command."
            )
        )

    if not get_selected_hunk_patch_file_path().exists():
        exit_with_error(_("No selected hunk. Run 'start' first."))

    if get_line_changes_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_line_changes_json_file_path()))
        file_path = data["path"]
        if snapshots_are_stale(file_path):
            clear_selected_change_state_files()
            exit_with_error(_("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."))


def recalculate_selected_hunk_for_file(
    file_path: str,
    *,
    auto_advance: bool | None = None,
) -> RecalculateSelectedHunkResult:
    """Recalculate the selected hunk for a specific file after modifications.

    After discard --line or include --line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.

    Args:
        file_path: Repository-relative path to recalculate hunk for
    """
    selected_kind = read_selected_change_kind()
    previous_line_changes = load_line_changes_from_state()
    if previous_line_changes is not None and previous_line_changes.path != file_path:
        previous_line_changes = None

    # Clear processed IDs since old line numbers don't apply to fresh hunk
    write_line_ids_file(get_processed_include_ids_file_path(), set())
    write_line_ids_file(get_processed_skip_ids_file_path(), set())

    if selected_kind == SelectedChangeKind.FILE:
        line_changes = cache_unstaged_file_as_single_hunk(file_path)
        if line_changes is None:
            clear_selected_change_state_files()
            if resolve_auto_advance(auto_advance):
                return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
            mark_selected_change_cleared_by_auto_advance_disabled()
            return RecalculateSelectedHunkResult.CLEARED

        line_changes = preserve_line_ids_from_previous_view(
            previous_line_changes,
            line_changes,
        )
        _write_line_changes_state(line_changes)

        if apply_line_level_batch_filter_to_cached_hunk():
            clear_selected_change_state_files()
            if resolve_auto_advance(auto_advance):
                return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
            mark_selected_change_cleared_by_auto_advance_disabled()
            return RecalculateSelectedHunkResult.CLEARED

        line_changes = load_line_changes_from_state()
        if line_changes is not None:
            print_line_level_changes(line_changes)
        return RecalculateSelectedHunkResult.RECALCULATED

    # Load blocklist
    blocked_hashes = read_text_file_line_set(get_block_list_file_path())

    # Stream git diff and parse incrementally - stops after first matching hunk found
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for single_hunk in patches:
                if single_hunk.old_path != file_path and single_hunk.new_path != file_path:
                    continue

                if isinstance(single_hunk, RenameChange):
                    rename_hash = compute_rename_change_hash(single_hunk)
                    if rename_hash in blocked_hashes:
                        continue
                    cache_rename_change(single_hunk)
                    print_rename_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, TextFileDeletionChange):
                    deletion_hash = compute_text_file_deletion_hash(single_hunk)
                    if deletion_hash in blocked_hashes or text_deletion_change_is_batched(single_hunk):
                        continue
                    cache_text_deletion_change(single_hunk)
                    print_text_file_deletion_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, GitlinkChange):
                    gitlink_hash = compute_gitlink_change_hash(single_hunk)
                    if gitlink_hash in blocked_hashes:
                        continue
                    cache_gitlink_change(single_hunk)
                    print_gitlink_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, BinaryFileChange):
                    continue

                if single_hunk.old_path != single_hunk.new_path:
                    rename_hash = compute_rename_change_hash(
                        RenameChange(
                            old_path=single_hunk.old_path,
                            new_path=single_hunk.new_path,
                        )
                    )
                    if rename_hash in blocked_hashes:
                        continue

                hunk_hash = compute_stable_hunk_hash_from_lines(single_hunk.lines)

                if hunk_hash in blocked_hashes:
                    continue

                write_selected_hunk_patch_lines(single_hunk.lines)
                write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)
                write_selected_change_kind(SelectedChangeKind.HUNK)

                line_changes = build_line_changes_from_patch_lines(
                    single_hunk.lines,
                    annotator=annotate_with_batch_source,
                )
                line_changes = preserve_line_ids_from_previous_view(
                    previous_line_changes,
                    line_changes,
                )
                _write_line_changes_state(line_changes)
                write_snapshots_for_selected_file_path(line_changes.path)

                # Apply batch filter to exclude batched lines
                if apply_line_level_batch_filter_to_cached_hunk():
                    # All lines were batched, clear the hunk
                    clear_selected_change_state_files()
                    print(_("No more lines in this hunk."), file=sys.stderr)
                    return RecalculateSelectedHunkResult.CLEARED

                # Display filtered hunk
                line_changes = load_line_changes_from_state()
                if line_changes is not None:
                    print_line_level_changes(line_changes)
                return RecalculateSelectedHunkResult.RECALCULATED
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        clear_selected_change_state_files()
        print(_("No pending hunks."), file=sys.stderr)
        return RecalculateSelectedHunkResult.CLEARED

    # No more hunks for this file, advance to next file
    clear_selected_change_state_files()
    if resolve_auto_advance(auto_advance):
        return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
    mark_selected_change_cleared_by_auto_advance_disabled()
    return RecalculateSelectedHunkResult.CLEARED
