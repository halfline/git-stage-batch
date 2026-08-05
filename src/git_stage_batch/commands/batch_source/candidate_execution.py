"""Reviewed candidate execution for batch-source action commands."""

from __future__ import annotations

import sys
from typing import Callable

from . import candidate_materialization as _candidate_materialization
from . import text_file_actions as _text_file_actions
from ...batch.operation_candidate_state import clear_candidate_preview_state_for_file
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...core.replacement import ReplacementPayload
from ...data.session import snapshot_file_if_untracked
from ...data.undo.checkpoints import UndoCheckpointStatus, undo_checkpoint
from ...i18n import _, bidi_isolate


def execute_apply_candidate(
    *,
    batch_name: str,
    raw_selector: str,
    ordinal: int,
    files: dict[str, BatchFileMetadataDict],
    selected_ids: set[int] | None,
    selection_ids_to_apply: set[int] | None,
    journal_progress: Callable[[str, str], None] | None = None,
) -> None:
    """Recompute and apply one previewed apply candidate."""
    report_progress = journal_progress or (lambda _stage, _rollback: None)
    report_progress("materialization", "not-started")
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
        print(
            f"  {bidi_isolate(file_path)}: {_('Working tree')}",
            file=sys.stderr,
        )
        operation_parts = ["apply", "--from", raw_selector, "--file", file_path]
        report_progress("checkpoint", "not-started")
        checkpoint_status: UndoCheckpointStatus | None = None
        publication_started = False
        try:
            with undo_checkpoint(
                " ".join(operation_parts),
                worktree_paths=[file_path],
                rollback_on_error=True,
            ) as checkpoint_status:
                publication_started = True
                report_progress("publication", checkpoint_status.rollback)
                snapshot_file_if_untracked(file_path)
                _text_file_actions.write_text_file_to_worktree(
                    file_path,
                    target.after_buffer,
                    materialized.file_mode,
                    target.change_type,
                )
        except BaseException:
            if publication_started:
                report_progress(
                    "publication",
                    (
                        checkpoint_status.rollback
                        if checkpoint_status is not None
                        else "unavailable"
                    ),
                )
            raise
        assert checkpoint_status is not None
        report_progress("publication", checkpoint_status.rollback)
        report_progress("completion", checkpoint_status.rollback)
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


def execute_include_candidate(
    *,
    batch_name: str,
    raw_selector: str,
    ordinal: int,
    files: dict[str, BatchFileMetadataDict],
    selected_ids: set[int] | None,
    selection_ids_to_include: set[int] | None,
    replacement_payload: ReplacementPayload | None,
) -> None:
    """Recompute and include one previewed include candidate."""
    materialized = _candidate_materialization.materialize_include_candidate(
        batch_name=batch_name,
        raw_selector=raw_selector,
        ordinal=ordinal,
        files=files,
        selected_ids=selected_ids,
        selection_ids_to_include=selection_ids_to_include,
        replacement_payload=replacement_payload,
    )
    try:
        preview = materialized.preview
        file_path = materialized.file_path
        index_target = materialized.index_target
        worktree_target = materialized.worktree_target
        print(
            _("Including candidate {ordinal} of {count} for batch '{batch}':").format(
                ordinal=preview.ordinal,
                count=preview.count,
                batch=batch_name,
            ),
            file=sys.stderr,
        )
        print(f"  {bidi_isolate(file_path)}:", file=sys.stderr)
        print(f"    {_('Index')}", file=sys.stderr)
        print(f"    {_('Working tree')}", file=sys.stderr)
        operation_parts = ["include", "--from", raw_selector, "--file", file_path]
        with undo_checkpoint(" ".join(operation_parts), worktree_paths=[file_path]):
            snapshot_file_if_untracked(file_path)
            _text_file_actions.stage_text_file_to_index(
                file_path,
                index_target.after_buffer,
                materialized.index_file_mode,
                index_target.change_type,
            )
            _text_file_actions.write_text_file_to_worktree(
                file_path,
                worktree_target.after_buffer,
                materialized.worktree_file_mode,
                worktree_target.change_type,
            )
        clear_candidate_preview_state_for_file(
            batch_name=batch_name,
            file_path=file_path,
        )
        print(
            _("✓ Included candidate {ordinal} of {count} from batch '{batch}'").format(
                ordinal=preview.ordinal,
                count=preview.count,
                batch=batch_name,
            ),
            file=sys.stderr,
        )
    finally:
        materialized.close()
