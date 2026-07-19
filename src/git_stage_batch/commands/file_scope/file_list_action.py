"""Live file-scope list action orchestration."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...core.hashing import compute_rename_change_hash
from ...core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    RenameChange,
    SingleHunkPatch,
    TextFileDeletionChange,
)
from ...data.binary_identity import attach_live_binary_fingerprint
from ...data.change_freshness import text_deletion_change_is_batched
from ...data.file_hunk_display import build_combined_file_line_changes
from ...data.file_tracking import auto_add_untracked_files
from ...data.live_diff import acquire_prepared_live_diff, group_live_diff_by_file
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
from ...utils.session_start_point import session_comparison_base
from ...utils.paths import get_context_lines


def show_live_file_list(files: list[str], *, selectable: bool = True) -> None:
    """Show a navigational file list for multiple live file reviews."""
    entries = []
    comparison_base = session_comparison_base()
    seen_rename_hashes: set[str] = set()
    auto_add_untracked_files(files)
    with ExitStack() as changes:
        comparison_changes = changes.enter_context(
            acquire_prepared_live_diff(
                base=comparison_base,
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        )
        comparison_changes_by_file = group_live_diff_by_file(
            files,
            comparison_changes,
        )
        needs_unstaged_atomic_diff = any(
            not any(
                isinstance(change, SingleHunkPatch)
                for change in comparison_changes_by_file[file_path]
            )
            for file_path in files
        )
        unstaged_changes = (
            changes.enter_context(
                acquire_prepared_live_diff(
                    context_lines=get_context_lines(),
                    full_index=True,
                    ignore_submodules="none",
                    submodule_format="short",
                )
            )
            if needs_unstaged_atomic_diff
            else ()
        )
        unstaged_changes_by_file = group_live_diff_by_file(files, unstaged_changes)
        for file_path in files:
            comparison_file_changes = comparison_changes_by_file[file_path]
            unstaged_file_changes = unstaged_changes_by_file[file_path]
            line_changes = build_combined_file_line_changes(
                file_path,
                comparison_file_changes,
            )
            if line_changes is not None:
                entries.append(make_file_review_list_entry(line_changes))
                continue
            mode_change = next(
                (
                    change
                    for change in unstaged_file_changes
                    if isinstance(change, FileModeChange)
                ),
                None,
            )
            if mode_change is not None:
                entries.append(make_mode_file_review_list_entry(mode_change))
                continue
            deletion_change = next(
                (
                    change
                    for change in unstaged_file_changes
                    if isinstance(change, TextFileDeletionChange)
                ),
                None,
            )
            if deletion_change is not None and not text_deletion_change_is_batched(
                deletion_change
            ):
                entries.append(
                    make_text_deletion_file_review_list_entry(deletion_change)
                )
                continue
            binary_change = next(
                (
                    change
                    for change in comparison_file_changes
                    if isinstance(change, BinaryFileChange)
                ),
                None,
            )
            if binary_change is not None:
                binary_change = attach_live_binary_fingerprint(
                    binary_change,
                    comparison_base=comparison_base,
                )
                entries.append(make_binary_file_review_list_entry(binary_change))
                continue
            gitlink_change = next(
                (
                    change
                    for change in comparison_file_changes
                    if isinstance(change, GitlinkChange)
                ),
                None,
            )
            if gitlink_change is not None:
                entries.append(make_gitlink_file_review_list_entry(gitlink_change))
                continue
            rename_change = next(
                (
                    change
                    for change in unstaged_file_changes
                    if isinstance(change, RenameChange)
                ),
                None,
            )
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
