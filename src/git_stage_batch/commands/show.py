"""Show command implementation."""

from __future__ import annotations

import sys

from ..data.selected_change.store import (
    SelectedChangeKind,
)
from ..data.selected_change.paths import get_selected_change_file_path
from ..data.selected_change.file_changes import (
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rename_change,
    cache_text_deletion_change,
)
from ..data.change_freshness import text_deletion_change_is_batched
from ..data.file_hunk_display import cache_file_as_single_hunk, render_file_as_single_hunk
from ..data.file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_rename_change,
    render_text_deletion_change,
)
from ..data.file_review.pages import normalize_page_spec
from ..data.file_review.records import ReviewSource
from ..data.file_review.state import (
    clear_last_file_review_state,
    write_last_file_review_state,
)
from ..data.line_state import load_line_changes_from_state
from ..data.session import require_session_started
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
    print_file_review,
)
from ..output.file_review_model_builder import build_file_review_model
from ..output.file_review_state_builder import (
    make_file_review_state,
    resolve_default_review_pages,
)
from ..utils.git_repository import require_git_repository
from ..utils.paths import (
    ensure_state_directory_exists,
)
from .file_scope import file_list_action as _file_list_action
from .selection.next_change_display import show_next_unprocessed_change


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

    _file_list_action.show_live_file_list(files, selectable=selectable)


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

    show_next_unprocessed_change(porcelain=porcelain, selectable=selectable)
