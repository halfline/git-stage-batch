"""Selected-change lifecycle helpers shared by command workflows."""

from __future__ import annotations

from .store import clear_selected_change_persistence_files


def clear_selected_change_state_files() -> None:
    """Clear selected change state and dependent file-review state."""
    from ..file_review.state import clear_last_file_review_state

    clear_selected_change_persistence_files()
    clear_last_file_review_state()
