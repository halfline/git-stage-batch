"""Show command implementation."""

from __future__ import annotations

import json
import sys

from ..batch.display import annotate_with_batch_source
from ..core.diff_parser import acquire_unified_diff, build_line_changes_from_patch_lines
from ..core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ..core.models import BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ..data.selected_change.hunk_filtering import (
    apply_line_level_batch_filter_to_cached_hunk,
)
from ..data.selected_change.store import (
    SelectedChangeKind,
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rename_change,
    cache_text_deletion_change,
    get_selected_change_file_path,
    mark_selected_change_cleared_by_file_list,
    restore_selected_change_state,
    snapshot_selected_change_state,
    write_selected_hunk_patch_lines,
    write_selected_change_kind,
)
from ..data.change_freshness import text_deletion_change_is_batched
from ..data.file_hunk_display import cache_file_as_single_hunk, render_file_as_single_hunk
from ..data.file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_rename_change,
    render_text_deletion_change,
)
from ..data.file_review.records import ReviewSource
from ..data.file_review.state import (
    clear_last_file_review_state,
    write_last_file_review_state,
)
from ..data.line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from ..data.live_diff import stream_live_git_diff
from ..data.session import require_session_started
from ..data.selected_change.lifecycle import clear_selected_change_state_files
from ..data.selected_change.snapshots import write_snapshots_for_selected_file_path
from ..exceptions import exit_with_error
from ..i18n import _
from ..output.hunk import print_line_level_changes
from ..output.patch import (
    print_binary_file_change,
    print_gitlink_change,
    print_rename_change,
    print_text_file_deletion_change,
)
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
    make_gitlink_file_review_list_entry,
    make_rename_file_review_list_entry,
    make_text_deletion_file_review_list_entry,
    print_file_review_list,
)
from ..utils.file_io import (
    read_text_file_line_set,
    write_text_file_contents,
)
from ..utils.git import require_git_repository
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


def command_show_file_list(files: list[str], *, selectable: bool = True) -> None:
    """Show a navigational file list for multiple live file reviews."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    entries = []
    seen_rename_hashes: set[str] = set()
    for file_path in files:
        line_changes = render_file_as_single_hunk(file_path)
        if line_changes is not None:
            entries.append(make_file_review_list_entry(line_changes))
            continue
        deletion_change = render_text_deletion_change(file_path)
        if deletion_change is not None and not text_deletion_change_is_batched(deletion_change):
            entries.append(make_text_deletion_file_review_list_entry(deletion_change))
            continue
        binary_change = render_binary_file_change(file_path)
        if binary_change is not None:
            entries.append(make_binary_file_review_list_entry(binary_change))
            continue
        gitlink_change = render_gitlink_change(file_path)
        if gitlink_change is not None:
            entries.append(make_gitlink_file_review_list_entry(gitlink_change))
            continue
        rename_change = render_rename_change(file_path)
        if rename_change is not None:
            rename_hash = compute_rename_change_hash(rename_change)
            if rename_hash not in seen_rename_hashes:
                entries.append(make_rename_file_review_list_entry(rename_change))
                seen_rename_hashes.add(rename_hash)

    if not entries:
        print(_("No reviewable changes in matched files."), file=sys.stderr)
        return

    if selectable:
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
        deletion_change = render_text_deletion_change(target_file) if preview_lines is None else None
        if deletion_change is not None and text_deletion_change_is_batched(deletion_change):
            deletion_change = None
        binary_change = (
            render_binary_file_change(target_file)
            if preview_lines is None and deletion_change is None else
            None
        )
        gitlink_change = (
            render_gitlink_change(target_file)
            if preview_lines is None and deletion_change is None and binary_change is None else
            None
        )
        rename_change = (
            render_rename_change(target_file)
            if preview_lines is None and deletion_change is None and binary_change is None and gitlink_change is None else
            None
        )
        if deletion_change is not None:
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_last_file_review_state()
                cache_text_deletion_change(deletion_change)
            if porcelain:
                return
            print_text_file_deletion_change(deletion_change)
            return
        if rename_change is not None:
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_last_file_review_state()
                cache_rename_change(rename_change)
            if porcelain:
                return
            print_rename_change(rename_change)
            return
        if gitlink_change is not None:
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_last_file_review_state()
                cache_gitlink_change(gitlink_change)
            if porcelain:
                return
            print_gitlink_change(gitlink_change)
            return
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
                if page is not None:
                    review_model = build_file_review_model(file_lines, gutter_to_selection_id={})
                    shown_pages = resolve_default_review_pages(
                        review_model,
                        requested_page_spec=page,
                        previous_selection=previous_selection,
                    )
                    page_spec = normalize_page_spec(shown_pages, len(review_model.pages))
                    print_file_review(
                        review_model,
                        shown_pages=shown_pages,
                        source_label=_("Changes: file vs HEAD"),
                        page_spec=page_spec,
                        source=ReviewSource.FILE_VS_HEAD,
                    )
                else:
                    print_line_level_changes(file_lines, gutter_to_selection_id={})
        return

    preview_state = snapshot_selected_change_state() if not selectable else None
    try:
        # Hunk-scoped operation (selected behavior)
        # Load blocklist
        blocklist_path = get_block_list_file_path()
        blocked_hashes = read_text_file_line_set(blocklist_path)

        # Stream diff and show first unblocked hunk
        with acquire_unified_diff(
            stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    rename_hash = compute_rename_change_hash(patch)
                    if rename_hash not in blocked_hashes:
                        cache_rename_change(patch)
                        if selectable:
                            clear_last_file_review_state()
                        if not porcelain:
                            print_rename_change(patch)
                        return
                    continue

                if isinstance(patch, TextFileDeletionChange):
                    deletion_hash = compute_text_file_deletion_hash(patch)
                    if deletion_hash not in blocked_hashes and not text_deletion_change_is_batched(patch):
                        cache_text_deletion_change(patch)
                        if selectable:
                            clear_last_file_review_state()
                        if not porcelain:
                            print_text_file_deletion_change(patch)
                        return
                    continue

                if isinstance(patch, GitlinkChange):
                    gitlink_hash = compute_gitlink_change_hash(patch)
                    if gitlink_hash not in blocked_hashes:
                        cache_gitlink_change(patch)
                        if selectable:
                            clear_last_file_review_state()
                        if not porcelain:
                            print_gitlink_change(patch)
                        return
                    continue

                if isinstance(patch, BinaryFileChange):
                    binary_hash = compute_binary_file_hash(patch)
                    if binary_hash not in blocked_hashes:
                        cache_binary_file_change(patch)
                        if selectable:
                            clear_last_file_review_state()
                        if not porcelain:
                            print_binary_file_change(patch)
                        return
                    continue

                if patch.old_path != patch.new_path:
                    rename_hash = compute_rename_change_hash(
                        RenameChange(old_path=patch.old_path, new_path=patch.new_path)
                    )
                    if rename_hash in blocked_hashes:
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

                    if selectable:
                        clear_last_file_review_state()

                    # Display this unprocessed hunk (unless porcelain mode)
                    if not porcelain:
                        line_changes = load_line_changes_from_state()
                        if line_changes is not None:
                            print_line_level_changes(
                                line_changes,
                                gutter_to_selection_id=None if selectable else {},
                            )
                    return

        # Either no changes or all hunks are blocked
        if porcelain:
            # Exit with code 1 for scripts
            sys.exit(1)
        else:
            print(_("No more hunks to process."), file=sys.stderr)
    finally:
        if preview_state is not None:
            restore_selected_change_state(preview_state)
            preview_state.close()
