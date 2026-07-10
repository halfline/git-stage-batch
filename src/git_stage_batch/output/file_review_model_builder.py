"""Model construction for file review output."""

from __future__ import annotations

from ..core.models import LineLevelChange, ReviewActionGroup
from .file_review_changes import build_file_review_changes
from .file_review_model import FileReviewModel
from .file_review_model_selections import derive_file_review_actionable_selections
from .file_review_pagination import paginate_file_review_changes


def build_file_review_model(
    line_changes: LineLevelChange,
    *,
    gutter_to_selection_id: dict[int, int] | None = None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None = None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None = None,
) -> FileReviewModel:
    """Build a conservative change-first page model from a file-scoped hunk."""
    display_id_by_selection_id = (
        {
            selection_id: gutter_id
            for gutter_id, selection_id in gutter_to_selection_id.items()
        }
        if gutter_to_selection_id is not None else None
    )
    actionable_selections = derive_file_review_actionable_selections(
        line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
        actionable_selection_groups=actionable_selection_groups,
        review_action_groups=review_action_groups,
        display_id_by_selection_id=display_id_by_selection_id,
    )
    changes = build_file_review_changes(
        line_changes,
        actionable_selections,
        display_id_by_selection_id,
    )

    paged_changes, pages = paginate_file_review_changes(
        changes,
    )
    return FileReviewModel(
        line_changes=line_changes,
        changes=paged_changes,
        pages=pages,
        display_id_by_selection_id=display_id_by_selection_id,
        review_action_groups=review_action_groups or (),
    )
