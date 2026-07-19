"""Command-layer display for the next unprocessed change."""

from __future__ import annotations

import sys

from ...core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.file_review.state import clear_last_file_review_state
from ...data.live_change_candidates import (
    EligibleLiveChange,
    next_eligible_live_change,
)
from ...data.selected_change.file_changes import (
    cache_binary_file_change,
    cache_gitlink_change,
    cache_mode_change,
    cache_rename_change,
    cache_text_deletion_change,
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
    print_file_mode_change,
    print_gitlink_change,
    print_rename_change,
    print_text_file_deletion_change,
)


def _cache_candidate(candidate: EligibleLiveChange) -> None:
    change = candidate.change
    if isinstance(change, FileModeChange):
        cache_mode_change(change)
    elif isinstance(change, RenameChange):
        cache_rename_change(change)
    elif isinstance(change, TextFileDeletionChange):
        cache_text_deletion_change(change)
    elif isinstance(change, GitlinkChange):
        cache_gitlink_change(change)
    elif isinstance(change, BinaryFileChange):
        cache_binary_file_change(change)
    else:
        cache_hunk_change(
            candidate.raw_patch.lines,
            candidate.stable_hash,
            change,
        )


def _print_candidate(candidate: EligibleLiveChange, *, selectable: bool) -> None:
    change = candidate.change
    if isinstance(change, FileModeChange):
        print_file_mode_change(change)
    elif isinstance(change, RenameChange):
        print_rename_change(change)
    elif isinstance(change, TextFileDeletionChange):
        print_text_file_deletion_change(change)
    elif isinstance(change, GitlinkChange):
        print_gitlink_change(change)
    elif isinstance(change, BinaryFileChange):
        print_binary_file_change(change)
    else:
        print_line_level_changes(
            change,
            gutter_to_selection_id=None if selectable else {},
        )


def show_next_unprocessed_change(
    *,
    porcelain: bool = False,
    selectable: bool = True,
) -> None:
    """Show the first live change accepted by the shared eligibility policy."""
    preview_state = snapshot_selected_change_state() if not selectable else None
    try:
        candidate = next_eligible_live_change()
        if candidate is None:
            if porcelain:
                sys.exit(1)
            print(_("No more hunks to process."), file=sys.stderr)
            return

        with candidate:
            _cache_candidate(candidate)
            if selectable:
                clear_last_file_review_state()
            if not porcelain:
                _print_candidate(candidate, selectable=selectable)
    finally:
        if preview_state is not None:
            restore_selected_change_state(preview_state)
            preview_state.close()
