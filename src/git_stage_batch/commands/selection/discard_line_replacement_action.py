"""Live discard line replacement action orchestration."""

from __future__ import annotations

from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.file_review.action_scope import finish_review_scoped_line_action
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.store import (
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from ...data.undo import undo_checkpoint
from ..file_scope.target_path import require_file_scope_target_path
from . import discard_file_selection as _discard_file_selection
from . import discard_line_batching as _discard_line_batching


def discard_live_line_replacement_to_batch(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    review_state,
    no_edge_overlap: bool = False,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save replacement text to a batch, then discard selected live lines."""
    replacement_payload = coerce_replacement_payload(replacement_text)
    operation_parts = [
        "discard",
        "--to",
        batch_name,
        "--line",
        line_id_specification,
        "--as",
        replacement_payload.display_text or "<stdin>",
    ]
    if no_edge_overlap:
        operation_parts.append("--no-edge-overlap")
    if file is not None:
        operation_parts.extend(["--file", file])

    target_file = None
    with (
        undo_checkpoint(" ".join(operation_parts)),
        snapshot_selected_change_state() as saved_selected_state,
    ):
        preserve_selected_state = file not in (None, "")

        try:
            if file is None:
                require_selected_hunk()
            else:
                target_file = require_file_scope_target_path(file)
                _discard_file_selection.load_explicit_file_selection(target_file)

            _discard_line_batching.discard_lines_as_to_batch(
                batch_name,
                line_id_specification,
                replacement_text,
                no_edge_overlap=no_edge_overlap,
                quiet=quiet,
                auto_advance=auto_advance,
            )

            if preserve_selected_state:
                restore_selected_change_state(saved_selected_state)
        except Exception:
            restore_selected_change_state(saved_selected_state)
            raise

    if file is None:
        finish_review_scoped_line_action(review_state)
    else:
        assert target_file is not None
        finish_review_scoped_line_action(review_state, file_path=target_file)
