"""Selected batch state cleanup for batch-source mutations."""

from __future__ import annotations

from ...data.batch_selected_changes import (
    selected_batch_binary_matches_batch,
    selected_batch_gitlink_matches_batch,
)
from ...data.file_review.records import ReviewSource
from ...data.file_review.state import read_last_file_review_state
from ...data.selected_change.clear_reasons import (
    mark_selected_change_cleared_by_stale_batch_selection,
)
from ...data.selected_change.lifecycle import clear_selected_change_state_files
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)


def clear_selected_batch_state_after_batch_mutation(
    *,
    source_batch: str,
    dest_batch: str | None,
    affected_files: set[str],
) -> None:
    """Clear selected batch views that point at files changed by a mutation."""
    selected_kind = read_selected_change_kind()
    if selected_kind not in (
        SelectedChangeKind.BATCH_FILE,
        SelectedChangeKind.BATCH_BINARY,
        SelectedChangeKind.BATCH_GITLINK,
    ):
        return

    selected_file = get_selected_change_file_path()
    if selected_file is None or selected_file not in affected_files:
        return

    if selected_kind == SelectedChangeKind.BATCH_BINARY:
        _clear_selected_binary_batch_state(
            source_batch=source_batch,
            dest_batch=dest_batch,
            selected_file=selected_file,
        )
        return
    if selected_kind == SelectedChangeKind.BATCH_GITLINK:
        _clear_selected_gitlink_batch_state(
            source_batch=source_batch,
            dest_batch=dest_batch,
            selected_file=selected_file,
        )
        return

    review_state = read_last_file_review_state()
    if review_state is not None:
        if (
            review_state.source == ReviewSource.BATCH
            and review_state.batch_name in {source_batch, dest_batch}
        ):
            stale_batch = review_state.batch_name or source_batch
            clear_selected_change_state_files()
            mark_selected_change_cleared_by_stale_batch_selection(
                batch_name=stale_batch,
                file_path=selected_file,
            )
        return

    # Filtered batch text views do not persist the batch name, so clear on a
    # matching path rather than leave a stale pathless action target behind.
    clear_selected_change_state_files()
    mark_selected_change_cleared_by_stale_batch_selection(
        batch_name=source_batch,
        file_path=selected_file,
    )


def _clear_selected_binary_batch_state(
    *,
    source_batch: str,
    dest_batch: str | None,
    selected_file: str,
) -> None:
    source_matches = selected_batch_binary_matches_batch(source_batch)
    dest_matches = (
        dest_batch is not None and selected_batch_binary_matches_batch(dest_batch)
    )
    if not source_matches and not dest_matches:
        return
    stale_batch = source_batch if source_matches else dest_batch
    clear_selected_change_state_files()
    mark_selected_change_cleared_by_stale_batch_selection(
        batch_name=stale_batch or source_batch,
        file_path=selected_file,
    )


def _clear_selected_gitlink_batch_state(
    *,
    source_batch: str,
    dest_batch: str | None,
    selected_file: str,
) -> None:
    source_matches = selected_batch_gitlink_matches_batch(source_batch)
    dest_matches = (
        dest_batch is not None and selected_batch_gitlink_matches_batch(dest_batch)
    )
    if not source_matches and not dest_matches:
        return
    stale_batch = source_batch if source_matches else dest_batch
    clear_selected_change_state_files()
    mark_selected_change_cleared_by_stale_batch_selection(
        batch_name=stale_batch or source_batch,
        file_path=selected_file,
    )
