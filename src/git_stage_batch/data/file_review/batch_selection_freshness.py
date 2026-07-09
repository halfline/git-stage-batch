"""Fresh batch-review selection lookup for batch line actions."""

from __future__ import annotations

import shlex

from ...exceptions import CommandError
from ...i18n import _
from . import records as _records
from .freshness import review_state_matches_action as _review_state_matches_action
from .selection_validation import (
    shown_review_selections_for_action as _shown_review_selections_for_action,
)
from .state import read_last_file_review_state


def fresh_batch_review_selections_for_action(
    batch_name: str,
    file_path: str,
    action: _records.FileReviewAction | str,
) -> list[_records.FileReviewSelectionState] | None:
    """Return shown review selections for a fresh matching batch review."""
    review_state = read_last_file_review_state()
    if review_state is None:
        return None
    if review_state.source != _records.ReviewSource.BATCH:
        return None
    if review_state.batch_name != batch_name or review_state.file_path != file_path:
        return None
    review_action = _records.coerce_review_action(action)
    try:
        review_is_fresh = _review_state_matches_action(review_state, review_action)
    except Exception:
        review_is_fresh = False
    if not review_is_fresh:
        raise CommandError(
            _(
                "The file review for {file} no longer matches batch '{batch}'.\n"
                "Line IDs may no longer match.\n\n"
                "Run:\n"
                "  git-stage-batch show --from {batch} --file {file}"
            ).format(
                batch=shlex.quote(batch_name),
                file=shlex.quote(file_path),
            )
        )

    return _shown_review_selections_for_action(review_state, action)
