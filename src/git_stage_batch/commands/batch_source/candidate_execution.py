"""Reviewed candidate execution for batch-source action commands."""

from __future__ import annotations

from functools import partial
import sys
from typing import Callable

from . import action_plans as _action_plans
from . import candidate_materialization as _candidate_materialization
from . import text_file_actions as _text_file_actions
from ...batch.operation_candidate_state import clear_candidate_preview_state_for_file
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...core.replacement import ReplacementPayload
from ...data.session import snapshot_file_if_untracked
from ...data.session_marker import session_is_active
from ...data.applied_batch_overlays import (
    applied_batch_overlays_repository_path,
    build_applied_file_provenance,
    record_applied_batch_overlays,
)
from ...data.file_target_identity import IndexIdentity
from ...data.file_target_identity import WorktreeIdentity
from ...data.file_target_identity import capture_worktree_identity
from ...data.file_target_identity import read_index_identities
from ...data.undo.checkpoints import (
    UndoCheckpointStatus,
    transaction_checkpoint,
)
from ...exceptions import CommandError
from ...git_paths import display_path, terminal_safe_shell_join
from ...i18n import _, bidi_isolate


def _finish_apply_candidate_success(
    *,
    batch_name: str,
    file_path: str,
    ordinal: int,
    count: int,
) -> None:
    """Publish apply-candidate completion after its outermost commit."""
    clear_candidate_preview_state_for_file(
        batch_name=batch_name,
        file_path=file_path,
    )
    print(
        _(
            "✓ Applied candidate {ordinal} of {count} from batch "
            "'{batch}' to working tree"
        ).format(
            ordinal=ordinal,
            count=count,
            batch=batch_name,
        ),
        file=sys.stderr,
    )


def _finish_include_candidate_success(
    *,
    batch_name: str,
    file_path: str,
    ordinal: int,
    count: int,
) -> None:
    """Publish include-candidate completion after its outermost commit."""
    clear_candidate_preview_state_for_file(
        batch_name=batch_name,
        file_path=file_path,
    )
    print(
        _("✓ Included candidate {ordinal} of {count} from batch '{batch}'").format(
            ordinal=ordinal,
            count=count,
            batch=batch_name,
        ),
        file=sys.stderr,
    )


def execute_apply_candidate(
    *,
    batch_name: str,
    batch_revision: str,
    raw_selector: str,
    ordinal: int,
    files: dict[str, BatchFileMetadataDict],
    selected_ids: set[int] | None,
    selection_ids_to_apply: set[int] | None,
    journal_progress: Callable[[str, str], None] | None = None,
) -> None:
    """Recompute and apply one previewed apply candidate."""
    initial_file_path = next(iter(files)) if len(files) == 1 else None
    expected_worktree_identity = (
        capture_worktree_identity(initial_file_path)
        if initial_file_path is not None
        else None
    )
    expected_index_identity = (
        read_index_identities((initial_file_path,))[initial_file_path]
        if initial_file_path is not None
        else None
    )
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
    with _action_plans.resource_cleanup((materialized,)) as close_materialized:
        target = materialized.target
        preview = materialized.preview
        file_path = materialized.file_path
        if (
            expected_worktree_identity is None
            or expected_index_identity is None
            or file_path != initial_file_path
        ):
            raise ValueError("apply candidate omitted its target identity")
        _require_unchanged_apply_candidate_targets(
            file_path,
            expected_index_identity,
            expected_worktree_identity,
        )
        print(
            _("Applying candidate {ordinal} of {count} from batch '{batch}':").format(
                ordinal=preview.ordinal,
                count=preview.count,
                batch=batch_name,
            ),
            file=sys.stderr,
        )
        print(
            "  {}: {}".format(
                bidi_isolate(display_path(file_path)),
                _("Working tree"),
            ),
            file=sys.stderr,
        )
        operation_parts = ["apply", "--from", raw_selector, "--file", file_path]
        before_identity = expected_worktree_identity
        file_provenance = build_applied_file_provenance(
            batch_name,
            file_path,
            files[file_path],
            selection_ids_to_apply,
            selected_file_metadata=materialized.selected_file_metadata,
            before_lines=target.before_buffer,
            after_lines=target.after_buffer,
        )
        report_progress("checkpoint", "not-started")
        checkpoint_status: UndoCheckpointStatus | None = None
        publication_started = False
        try:
            with transaction_checkpoint(
                terminal_safe_shell_join(operation_parts),
                worktree_paths=[file_path],
                repository_paths=[applied_batch_overlays_repository_path()],
            ) as checkpoint_status:
                _require_unchanged_apply_candidate_targets(
                    file_path,
                    expected_index_identity,
                    expected_worktree_identity,
                )
                checkpoint_status.arm_rollback()
                publication_started = True
                report_progress("publication", checkpoint_status.rollback)
                if session_is_active():
                    snapshot_file_if_untracked(file_path)
                _text_file_actions.write_text_file_to_worktree(
                    file_path,
                    target.after_buffer,
                    materialized.file_mode,
                    target.change_type,
                )
                record_applied_batch_overlays(
                    batch_name=batch_name,
                    batch_revision=batch_revision,
                    files={file_path: file_provenance},
                    before_worktree_identities={file_path: before_identity},
                    expected_index_identities={
                        file_path: expected_index_identity,
                    },
                )
                close_materialized()
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
        checkpoint_status.defer_success(
            partial(
                _finish_apply_candidate_success,
                batch_name=batch_name,
                file_path=file_path,
                ordinal=preview.ordinal,
                count=preview.count,
            )
        )


def _require_unchanged_apply_candidate_targets(
    file_path: str,
    expected_index_identity: IndexIdentity,
    expected_worktree_identity: WorktreeIdentity,
) -> None:
    """Refuse a candidate computed from a target that changed."""
    current_index_identity = read_index_identities((file_path,))[file_path]
    if (
        expected_index_identity.unmerged_entries
        or current_index_identity != expected_index_identity
    ):
        raise CommandError(
            _(
                "Index changed while apply was being calculated: "
                "{file}. Retry the apply command."
            ).format(file=display_path(file_path))
        )
    if capture_worktree_identity(file_path) != expected_worktree_identity:
        raise CommandError(
            _(
                "Working tree file changed while apply was being calculated: "
                "{file}. Retry the apply command."
            ).format(file=display_path(file_path))
        )


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
    initial_file_path = next(iter(files)) if len(files) == 1 else None
    expected_worktree_identity = (
        capture_worktree_identity(initial_file_path)
        if initial_file_path is not None
        else None
    )
    expected_index_identity = (
        read_index_identities((initial_file_path,))[initial_file_path]
        if initial_file_path is not None
        else None
    )
    materialized = _candidate_materialization.materialize_include_candidate(
        batch_name=batch_name,
        raw_selector=raw_selector,
        ordinal=ordinal,
        files=files,
        selected_ids=selected_ids,
        selection_ids_to_include=selection_ids_to_include,
        replacement_payload=replacement_payload,
    )
    with _action_plans.resource_cleanup((materialized,)) as close_materialized:
        preview = materialized.preview
        file_path = materialized.file_path
        index_target = materialized.index_target
        worktree_target = materialized.worktree_target
        if (
            expected_worktree_identity is None
            or expected_index_identity is None
            or file_path != initial_file_path
        ):
            raise ValueError("include candidate omitted its target identity")
        _require_unchanged_include_candidate_targets(
            file_path,
            expected_index_identity,
            expected_worktree_identity,
        )
        print(
            _("Including candidate {ordinal} of {count} for batch '{batch}':").format(
                ordinal=preview.ordinal,
                count=preview.count,
                batch=batch_name,
            ),
            file=sys.stderr,
        )
        print(f"  {bidi_isolate(display_path(file_path))}:", file=sys.stderr)
        print("    {}".format(_("Index")), file=sys.stderr)
        print("    {}".format(_("Working tree")), file=sys.stderr)
        operation_parts = ["include", "--from", raw_selector, "--file", file_path]
        with transaction_checkpoint(
            terminal_safe_shell_join(operation_parts),
            worktree_paths=[file_path],
            index_paths=[file_path],
        ) as checkpoint_status:
            _require_unchanged_include_candidate_targets(
                file_path,
                expected_index_identity,
                expected_worktree_identity,
            )
            checkpoint_status.arm_rollback()
            if session_is_active():
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
            close_materialized()
        checkpoint_status.defer_success(
            partial(
                _finish_include_candidate_success,
                batch_name=batch_name,
                file_path=file_path,
                ordinal=preview.ordinal,
                count=preview.count,
            )
        )


def _require_unchanged_include_candidate_targets(
    file_path: str,
    expected_index_identity: IndexIdentity,
    expected_worktree_identity: WorktreeIdentity,
) -> None:
    """Refuse an include candidate computed from a target that changed."""
    current_index_identity = read_index_identities((file_path,))[file_path]
    if (
        expected_index_identity.unmerged_entries
        or current_index_identity != expected_index_identity
    ):
        raise CommandError(
            _(
                "Index changed while include was being calculated: "
                "{file}. Retry the include command."
            ).format(file=display_path(file_path))
        )
    if capture_worktree_identity(file_path) != expected_worktree_identity:
        raise CommandError(
            _(
                "Working tree file changed while include was being calculated: "
                "{file}. Retry the include command."
            ).format(file=display_path(file_path))
        )
