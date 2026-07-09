"""Reviewed candidate execution for batch-source action commands."""

from __future__ import annotations

import sys

from . import candidate_materialization as _candidate_materialization
from . import text_file_actions as _text_file_actions
from ...batch.operation_candidates import clear_candidate_preview_state_for_file
from ...data.session import snapshot_file_if_untracked
from ...data.undo import undo_checkpoint
from ...i18n import _


def execute_apply_candidate(
    *,
    batch_name: str,
    raw_selector: str,
    ordinal: int,
    files: dict,
    selected_ids: set[int] | None,
    selection_ids_to_apply: set[int] | None,
) -> None:
    """Recompute and apply one previewed apply candidate."""
    materialized = _candidate_materialization.materialize_apply_candidate(
        batch_name=batch_name,
        raw_selector=raw_selector,
        ordinal=ordinal,
        files=files,
        selected_ids=selected_ids,
        selection_ids_to_apply=selection_ids_to_apply,
    )
    try:
        target = materialized.target
        preview = materialized.preview
        file_path = materialized.file_path
        print(
            _("Applying candidate {ordinal} of {count} from batch '{batch}':").format(
                ordinal=preview.ordinal,
                count=preview.count,
                batch=batch_name,
            ),
            file=sys.stderr,
        )
        print(f"  {file_path}: {_('Working tree')}", file=sys.stderr)
        operation_parts = ["apply", "--from", raw_selector, "--file", file_path]
        with undo_checkpoint(" ".join(operation_parts), worktree_paths=[file_path]):
            snapshot_file_if_untracked(file_path)
            _text_file_actions.write_text_file_to_worktree(
                file_path,
                target.after_buffer,
                materialized.file_mode,
                target.change_type,
            )
        clear_candidate_preview_state_for_file(
            batch_name=batch_name,
            file_path=file_path,
        )
        print(
            _(
                "✓ Applied candidate {ordinal} of {count} from batch "
                "'{batch}' to working tree"
            ).format(
                ordinal=preview.ordinal,
                count=preview.count,
                batch=batch_name,
            ),
            file=sys.stderr,
        )
    finally:
        materialized.close()
