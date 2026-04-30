"""Show command implementation."""

from __future__ import annotations

import json
import sys

from ..batch.display import annotate_with_batch_source
from ..core.diff_parser import build_line_changes_from_patch_bytes, parse_unified_diff_streaming
from ..core.diff_parser import write_snapshots_for_selected_file_path
from ..core.hashing import compute_stable_hunk_hash
from ..core.models import BinaryFileChange
from ..data.hunk_tracking import (
    SelectedChangeKind,
    apply_line_level_batch_filter_to_cached_hunk,
    cache_file_as_single_hunk,
    get_selected_change_file_path,
    render_file_as_single_hunk,
    write_selected_change_kind,
)
from ..data.line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from ..data.session import require_session_started
from ..exceptions import exit_with_error
from ..i18n import _
from ..output import print_line_level_changes
from ..utils.file_io import read_text_file_contents, write_file_bytes, write_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)


def command_show(file: str | None = None, *, porcelain: bool = False, selectable: bool = True) -> None:
    """Show the first unprocessed hunk or entire file.

    Args:
        file: Optional file path for file-scoped display.
              If empty string, uses selected hunk's file.
              If None, shows selected hunk (normal behavior).
        porcelain: If True, produce no output and exit with code 0 if hunk found, 1 if none
        selectable: If True, cache the file and show selectable gutter IDs.
                    If False, only preview the file and hide gutter IDs.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # File-scoped operation
    if file is not None:
        # Determine target file
        if file == "":
            # --file with no arg: use selected hunk's file
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file

        # Cache and display entire file when it's the active selection.
        file_lines = (
            cache_file_as_single_hunk(target_file)
            if selectable else
            render_file_as_single_hunk(target_file)
        )
        if file_lines is None:
            if porcelain:
                sys.exit(1)
            else:
                print(_("No changes in file '{file}'.").format(file=target_file), file=sys.stderr)
            return

        if not porcelain:
            print_line_level_changes(file_lines, gutter_to_selection_id=None if selectable else {})
        return

    # Hunk-scoped operation (selected behavior)
    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Stream diff and show first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        # Skip binary files for now (they need special handling)
        if isinstance(patch, BinaryFileChange):
            continue

        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)
        if patch_hash not in blocked_hashes:
            # Cache selected hunk bytes exactly; display text is derived from parsed lines.
            write_file_bytes(get_selected_hunk_patch_file_path(), patch_bytes)
            write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
            write_selected_change_kind(SelectedChangeKind.HUNK)

            # Parse and cache line_changes for batch filtering
            line_changes = build_line_changes_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
            write_text_file_contents(get_line_changes_json_file_path(),
                                    json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                              ensure_ascii=False, indent=0))
            write_snapshots_for_selected_file_path(line_changes.path)

            # Apply line-level batch filtering
            if apply_line_level_batch_filter_to_cached_hunk():
                # All lines in this hunk are batched, skip to next
                continue

            # Display this unprocessed hunk (unless porcelain mode)
            if not porcelain:
                line_changes = load_line_changes_from_state()
                if line_changes is not None:
                    print_line_level_changes(line_changes)
            return

    # Either no changes or all hunks are blocked
    if porcelain:
        # Exit with code 1 for scripts
        sys.exit(1)
    else:
        print(_("No more hunks to process."), file=sys.stderr)
