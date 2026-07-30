"""Hunk navigation, selected-state orchestration, and progress tracking."""

from __future__ import annotations

from typing import Union

from ..core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    LineLevelChange,
    RenameChange,
    SingleHunkPatch,
    TextFileDeletionChange,
)
from ..exceptions import NoMoreHunks
from .auto_advance import resolve_auto_advance
from .selected_change import clear_reasons as _selected_change_clear_reasons
from .selected_change import file_changes as _selected_file_changes
from .selected_change import store as _selected_store
from .selected_change.lifecycle import (
    clear_selected_change_state_files as _clear_selected_change_state_files,
)
from .live_change_candidates import next_eligible_live_change


def fetch_next_change() -> Union[
    LineLevelChange,
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
]:
    """Find the next hunk or binary file that isn't blocked and cache it as selected.

    Returns:
        LineLevelChange for text hunks, BinaryFileChange for binary files.

    Raises:
        NoMoreHunks: When there are no more items to process.
    """
    candidate = next_eligible_live_change()
    if candidate is not None:
        with candidate:
            item = candidate.change
            if isinstance(item, FileModeChange):
                _selected_file_changes.cache_mode_change(item)
            elif isinstance(item, RenameChange):
                _selected_file_changes.cache_rename_change(item)
            elif isinstance(item, TextFileDeletionChange):
                _selected_file_changes.cache_text_deletion_change(item)
            elif isinstance(item, GitlinkChange):
                _selected_file_changes.cache_gitlink_change(item)
            elif isinstance(item, BinaryFileChange):
                _selected_file_changes.cache_binary_file_change(item)
            else:
                assert isinstance(candidate.raw_patch, SingleHunkPatch)
                _selected_store.cache_hunk_change(
                    candidate.raw_patch.lines,
                    candidate.stable_hash,
                    item,
                )
            return item

    # No more items to process
    raise NoMoreHunks()


def advance_to_next_change() -> None:
    """Clear selected hunk state and advance to the next unblocked hunk.

    If no more hunks exist, clears state and returns silently.
    """
    _clear_selected_change_state_files()
    try:
        fetch_next_change()
    except NoMoreHunks:
        # No more items - state is already cleared
        pass


def select_next_change_after_action(
    *,
    auto_advance: bool | None = None,
) -> bool:
    """Select the next hunk after an action, or leave selection empty."""
    if resolve_auto_advance(auto_advance):
        advance_to_next_change()
        return True

    _clear_selected_change_state_files()
    _selected_change_clear_reasons.mark_selected_change_cleared_by_auto_advance_disabled()
    return False
