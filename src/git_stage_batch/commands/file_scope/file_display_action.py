"""Live single-file display action orchestration."""

from __future__ import annotations

import sys

from ...data.change_freshness import text_deletion_change_is_batched
from ...data.file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_rename_change,
    render_mode_change,
    render_text_deletion_change,
)
from ...data.file_hunk_display import render_file_as_single_hunk
from ...data.selected_change.file_hunk_cache import cache_file_as_single_hunk
from ...data.file_review.pages import normalize_page_spec
from ...data.file_review.records import ReviewSource
from ...data.file_review.state import (
    clear_last_file_review_state,
    write_last_file_review_state,
)
from ...data.line_state import load_line_changes_from_state
from ...data.selected_change.file_changes import (
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rename_change,
    cache_mode_change,
    cache_text_deletion_change,
)
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import SelectedChangeKind
from ...exceptions import exit_with_error
from ...i18n import _
from ...output.file_review import print_file_review
from ...output.file_review_model_builder import build_file_review_model
from ...output.file_review_state_builder import (
    make_file_review_state,
    resolve_default_review_pages,
)
from ...output.hunk import print_line_level_changes
from ...output.patch import (
    print_binary_file_change,
    print_gitlink_change,
    print_rename_change,
    print_file_mode_change,
    print_text_file_deletion_change,
)
from ...utils.session_start_point import session_comparison_base


def _load_previous_selection_for_review():
    """Best-effort load of the prior selection for page anchoring."""
    try:
        return load_line_changes_from_state()
    except Exception:
        return None


def show_live_file_display(
    file_arg: str,
    *,
    page: str | None,
    porcelain: bool,
    selectable: bool,
) -> None:
    """Show one live file-scope review."""
    previous_selection = _load_previous_selection_for_review()

    if file_arg == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
    else:
        target_file = file_arg

    preview_lines = render_file_as_single_hunk(target_file)
    comparison_base = session_comparison_base()
    deletion_change = (
        render_text_deletion_change(target_file) if preview_lines is None else None
    )
    if deletion_change is not None and text_deletion_change_is_batched(
        deletion_change
    ):
        deletion_change = None
    binary_change = (
        render_binary_file_change(target_file, base=comparison_base)
        if preview_lines is None and deletion_change is None
        else None
    )
    gitlink_change = (
        render_gitlink_change(target_file, base=comparison_base)
        if preview_lines is None
        and deletion_change is None
        and binary_change is None
        else None
    )
    rename_change = (
        render_rename_change(target_file)
        if preview_lines is None
        and deletion_change is None
        and binary_change is None
        and gitlink_change is None
        else None
    )
    mode_change = render_mode_change(target_file) if preview_lines is None else None

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

    if mode_change is not None:
        if page is not None:
            exit_with_error(_("File review pages are only available for text changes."))
        if selectable:
            clear_last_file_review_state()
            cache_mode_change(mode_change)
        if porcelain:
            return
        print_file_mode_change(mode_change)
        return

    if gitlink_change is not None:
        if page is not None:
            exit_with_error(_("File review pages are only available for text changes."))
        if selectable:
            clear_last_file_review_state()
            cache_gitlink_change(
                gitlink_change,
                comparison_base=comparison_base,
            )
        if porcelain:
            return
        print_gitlink_change(gitlink_change)
        return

    if binary_change is not None:
        if page is not None:
            exit_with_error(_("File review pages are only available for text changes."))
        if selectable:
            clear_last_file_review_state()
            cache_binary_file_change(
                binary_change,
                comparison_base=comparison_base,
            )
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

    file_lines = cache_file_as_single_hunk(target_file) if selectable else preview_lines
    if file_lines is None:
        if porcelain:
            sys.exit(1)
        print(_("No changes in file '{file}'.").format(file=target_file), file=sys.stderr)
        return

    if selectable:
        clear_last_file_review_state()

    if porcelain:
        return

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
    elif page is not None:
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
