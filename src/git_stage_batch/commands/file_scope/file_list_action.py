"""Live file-scope list action orchestration."""

from __future__ import annotations

import sys

from ...core.hashing import compute_rename_change_hash
from ...data.change_freshness import text_deletion_change_is_batched
from ...data.file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_mode_change,
    render_rename_change,
    render_text_deletion_change,
)
from ...data.file_hunk_display import render_file_as_single_hunk
from ...data.file_review.records import ReviewSource
from ...data.selected_change.clear_reasons import (
    mark_selected_change_cleared_by_file_list,
)
from ...data.selected_change.lifecycle import clear_selected_change_state_files
from ...i18n import _
from ...output.file_review_list import (
    make_binary_file_review_list_entry,
    make_file_review_list_entry,
    make_gitlink_file_review_list_entry,
    make_mode_file_review_list_entry,
    make_rename_file_review_list_entry,
    make_text_deletion_file_review_list_entry,
    print_file_review_list,
)


def show_live_file_list(files: list[str], *, selectable: bool = True) -> None:
    """Show a navigational file list for multiple live file reviews."""
    entries = []
    seen_rename_hashes: set[str] = set()
    for file_path in files:
        line_changes = render_file_as_single_hunk(file_path)
        if line_changes is not None:
            entries.append(make_file_review_list_entry(line_changes))
            continue
        mode_change = render_mode_change(file_path)
        if mode_change is not None:
            entries.append(make_mode_file_review_list_entry(mode_change))
            continue
        deletion_change = render_text_deletion_change(file_path)
        if deletion_change is not None and not text_deletion_change_is_batched(
            deletion_change
        ):
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
        mark_selected_change_cleared_by_file_list(
            source=ReviewSource.FILE_VS_HEAD.value
        )

    print_file_review_list(
        source_label=_("Changes: file vs HEAD"),
        entries=entries,
    )
