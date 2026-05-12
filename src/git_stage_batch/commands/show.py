"""Show command implementation."""

from __future__ import annotations

import json
import sys

from ..batch.display import annotate_with_batch_source
from ..core.diff_parser import build_line_changes_from_patch_lines, parse_unified_diff_streaming
from ..core.diff_parser import write_snapshots_for_selected_file_path
from ..core.hashing import compute_binary_file_hash, compute_stable_hunk_hash_from_lines
from ..core.models import BinaryFileChange
from ..data.hunk_tracking import (
    SelectedChangeKind,
    apply_line_level_batch_filter_to_cached_hunk,
    cache_binary_file_change,
    cache_file_as_single_hunk,
    clear_selected_change_state_files,
    get_selected_change_file_path,
    mark_selected_change_cleared_by_file_list,
    render_binary_file_change,
    render_file_as_single_hunk,
    restore_selected_change_state,
    snapshot_selected_change_state,
    write_selected_hunk_patch_lines,
    write_selected_change_kind,
)
from ..data.file_review_state import (
    clear_last_file_review_state,
    ReviewSource,
    write_last_file_review_state,
)
from ..data.line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from ..data.session import require_session_started
from ..exceptions import exit_with_error
from ..i18n import _
from ..output import print_binary_file_change, print_line_level_changes
from ..output.file_review import (
    build_file_review_model,
    make_file_review_state,
    normalize_page_spec,
    print_file_review,
    resolve_default_review_pages,
)
from ..output.file_review_list import (
    make_binary_file_review_list_entry,
    make_file_review_list_entry,
    print_file_review_list,
)
from ..utils.file_io import (
    read_text_file_line_set,
    write_text_file_contents,
)
from ..utils.git import require_git_repository, stream_git_diff
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
)


def _load_previous_selection_for_review():
    """Best-effort load of the prior selection for page anchoring."""
    try:
        return load_line_changes_from_state()
    except Exception:
        return None


def command_show_file_list(files: list[str]) -> None:
    """Show a navigational file list for multiple live file reviews."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    entries = []
    for file_path in files:
        line_changes = render_file_as_single_hunk(file_path)
        if line_changes is not None:
            entries.append(make_file_review_list_entry(line_changes))
            continue
        binary_change = render_binary_file_change(file_path)
        if binary_change is not None:
            entries.append(make_binary_file_review_list_entry(binary_change))

    if not entries:
        print(_("No reviewable changes in matched files."), file=sys.stderr)
        return

    clear_selected_change_state_files()
    mark_selected_change_cleared_by_file_list(source=ReviewSource.FILE_VS_HEAD.value)

    print_file_review_list(
        source_label=_("Changes: file vs HEAD"),
        entries=entries,
    )


def command_show(
    file: str | None = None,
    *,
    page: str | None = None,
    porcelain: bool = False,
    selectable: bool = True,
) -> None:
    """Show the first unprocessed hunk or entire file.

    Args:
        file: Optional file path for file-scoped display.
              If empty string, uses selected hunk's file.
              If None, shows selected hunk (normal behavior).
        page: Optional file-review page selection.
        porcelain: If True, produce no output and exit with code 0 if hunk found, 1 if none
        selectable: If True, cache the file and show selectable gutter IDs.
                    If False, only preview the file and hide gutter IDs.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # File-scoped operation
    if file is not None:
        previous_selection = _load_previous_selection_for_review()

        # Determine target file
        if file == "":
            # --file with no arg: use selected hunk's file
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file

        preview_lines = render_file_as_single_hunk(target_file)
        binary_change = render_binary_file_change(target_file) if preview_lines is None else None
        if binary_change is not None:
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_last_file_review_state()
                cache_binary_file_change(binary_change)
            if porcelain:
                return
            print_binary_file_change(binary_change)
            return

        if selectable and page is not None and preview_lines is not None:
            preview_model = build_file_review_model(preview_lines)
            resolve_default_review_pages(
                preview_model,
                requested_page_spec=page,
                previous_selection=previous_selection,
            )

        # Cache and display entire file when it's the active selection.
        file_lines = (
            cache_file_as_single_hunk(target_file)
            if selectable else
            preview_lines
        )
        if file_lines is None:
            if porcelain:
                sys.exit(1)
            else:
                print(_("No changes in file '{file}'.").format(file=target_file), file=sys.stderr)
            return

        if selectable:
            clear_last_file_review_state()

        if porcelain:
            return

        if not porcelain:
            if selectable:
                review_model = build_file_review_model(file_lines)
                shown_pages = resolve_default_review_pages(
                    review_model,
                    requested_page_spec=page,
                    previous_selection=previous_selection,
                )
                page_spec = normalize_page_spec(shown_pages, len(review_model.pages))
                review_state = make_file_review_state(
                    review_model,
                    source=ReviewSource.FILE_VS_HEAD,
                    batch_name=None,
                    shown_pages=shown_pages,
                    selected_change_kind=SelectedChangeKind.FILE,
                )
                write_last_file_review_state(review_state)
                print_file_review(
                    review_model,
                    shown_pages=shown_pages,
                    source_label=_("Changes: file vs HEAD"),
                    page_spec=page_spec,
                    source=ReviewSource.FILE_VS_HEAD,
                    opened_near_selected_hunk=(
                        page is None
                        and previous_selection is not None
                        and previous_selection.path == file_lines.path
                        and len(review_model.pages) > 1
                    ),
                )
            else:
                print_line_level_changes(file_lines, gutter_to_selection_id={})
        return

    # Hunk-scoped operation (selected behavior)
    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocked_hashes = read_text_file_line_set(blocklist_path)

    # Stream diff and show first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_diff(context_lines=get_context_lines())):
        if isinstance(patch, BinaryFileChange):
            binary_hash = compute_binary_file_hash(patch)
            if binary_hash not in blocked_hashes:
                cache_binary_file_change(patch)
                clear_last_file_review_state()
                if not porcelain:
                    print_binary_file_change(patch)
                return
            continue

        patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)
        if patch_hash not in blocked_hashes:
            with snapshot_selected_change_state() as previous_selected_state:
                # Cache selected hunk bytes exactly; display text is derived from parsed lines.
                write_selected_hunk_patch_lines(patch.lines)
                write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
                write_selected_change_kind(SelectedChangeKind.HUNK)

                # Parse and cache line_changes for batch filtering
                line_changes = build_line_changes_from_patch_lines(
                    patch.lines,
                    annotator=annotate_with_batch_source,
                )
                write_text_file_contents(get_line_changes_json_file_path(),
                                        json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                                  ensure_ascii=False, indent=0))
                write_snapshots_for_selected_file_path(line_changes.path)

                # Apply line-level batch filtering
                if apply_line_level_batch_filter_to_cached_hunk():
                    # All lines in this hunk are batched, skip to next
                    restore_selected_change_state(previous_selected_state)
                    continue

            clear_last_file_review_state()

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
