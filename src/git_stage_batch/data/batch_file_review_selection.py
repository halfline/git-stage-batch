"""Review-aware batch file selection translation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..batch.file_display import render_batch_file_display
from ..exceptions import exit_with_error
from ..i18n import _
from .file_review.state import (
    fresh_batch_review_selections_for_action,
    validate_review_scoped_line_selection,
)

if TYPE_CHECKING:
    from ..core.models import RenderedBatchDisplay
    from .file_review.records import FileReviewAction


def translate_batch_file_gutter_ids_to_selection_ids(
    batch_name: str,
    file_path: str,
    selected_ids: set[int] | None,
    action: 'FileReviewAction | str',
) -> tuple[set[int] | None, 'RenderedBatchDisplay | None']:
    """Translate displayed batch-file gutter IDs to internal selection IDs.

    If the IDs came after a fresh matching file review, validate them against
    the complete actions shown by that review before consulting the full batch
    display. Without a matching review, keep the historical raw batch display
    behavior.
    """
    if selected_ids is None:
        return None, None

    review_selections = fresh_batch_review_selections_for_action(
        batch_name,
        file_path,
        action,
    )
    if review_selections is not None:
        validate_review_scoped_line_selection(selected_ids, review_selections)

    rendered = render_batch_file_display(batch_name, file_path)
    if rendered is None:
        return selected_ids, None

    display_id_map = (
        rendered.review_gutter_to_selection_id or rendered.gutter_to_selection_id
        if review_selections is not None else
        rendered.gutter_to_selection_id
    )
    rendered_for_messages = (
        replace(
            rendered,
            gutter_to_selection_id=dict(display_id_map),
            selection_id_to_gutter={
                selection_id: gutter_id
                for gutter_id, selection_id in display_id_map.items()
            },
        )
        if review_selections is not None else
        rendered
    )
    selection_ids: set[int] = set()
    for gutter_id in selected_ids:
        if gutter_id in display_id_map:
            selection_ids.add(display_id_map[gutter_id])
        else:
            exit_with_error(
                _("Line ID {id} is not available for this action. Select one of the numbered lines shown for this batch file.").format(
                    id=gutter_id
                )
            )

    return selection_ids, rendered_for_messages
