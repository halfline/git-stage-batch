"""Command-layer display for the next unprocessed change."""

from __future__ import annotations

import sys

from ...batch.source_annotation import annotate_with_batch_source
from ...core.diff_parser import acquire_unified_diff, build_line_changes_from_patch_lines
from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.change_freshness import text_deletion_change_is_batched
from ...data.file_review.state import clear_last_file_review_state
from ...data.line_state import load_line_changes_from_state
from ...data.live_diff import stream_live_git_diff
from ...data.selected_change.file_changes import (
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rename_change,
    cache_text_deletion_change,
)
from ...data.selected_change.hunk_filtering import (
    apply_line_level_batch_filter_to_cached_hunk,
)
from ...data.selected_change.store import (
    cache_hunk_change,
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from ...i18n import _
from ...output.hunk import print_line_level_changes
from ...output.patch import (
    print_binary_file_change,
    print_gitlink_change,
    print_rename_change,
    print_text_file_deletion_change,
)
from ...utils.file_io import read_text_file_line_set
from ...utils.paths import get_block_list_file_path, get_context_lines


def show_next_unprocessed_change(
    *,
    porcelain: bool = False,
    selectable: bool = True,
) -> None:
    """Show the first unprocessed hunk or file-level change."""
    preview_state = snapshot_selected_change_state() if not selectable else None
    try:
        blocklist_path = get_block_list_file_path()
        blocked_hashes = read_text_file_line_set(blocklist_path)

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
                    if (
                        deletion_hash not in blocked_hashes
                        and not text_deletion_change_is_batched(patch)
                    ):
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
                        line_changes = build_line_changes_from_patch_lines(
                            patch.lines,
                            annotator=annotate_with_batch_source,
                        )
                        cache_hunk_change(patch.lines, patch_hash, line_changes)

                        if apply_line_level_batch_filter_to_cached_hunk():
                            restore_selected_change_state(previous_selected_state)
                            continue

                    if selectable:
                        clear_last_file_review_state()

                    if not porcelain:
                        line_changes = load_line_changes_from_state()
                        if line_changes is not None:
                            print_line_level_changes(
                                line_changes,
                                gutter_to_selection_id=None if selectable else {},
                            )
                    return

        if porcelain:
            sys.exit(1)
        print(_("No more hunks to process."), file=sys.stderr)
    finally:
        if preview_state is not None:
            restore_selected_change_state(preview_state)
            preview_state.close()
