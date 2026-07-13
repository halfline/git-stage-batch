"""Include-from execution for batch-source action commands."""

from __future__ import annotations

import sys

from . import action_completion as _action_completion
from . import action_context as _action_context
from . import action_plans as _action_plans
from . import action_selection as _action_selection
from . import atomic_unit_refusals as _atomic_unit_refusals
from . import binary_file_actions as _binary_file_actions
from . import file_mode_actions as _file_mode_actions
from . import candidate_preview_counts as _candidate_preview_counts
from . import candidate_refusals as _candidate_refusals
from . import merge_refusals as _merge_refusals
from . import text_file_actions as _text_file_actions
from . import text_plan_builders as _text_plan_builders
from . import worktree_refusals as _worktree_refusals
from ...batch.binary_file_content import read_binary_file_from_batch
from ...batch.submodule_pointer import (
    is_batch_submodule_pointer,
    stage_submodule_pointer_from_batch,
)
from ...core.replacement import ReplacementPayload
from ...data.session import snapshot_file_if_untracked
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import (
    AtomicUnitError,
    CommandError,
    MergeError,
    exit_with_error,
)
from ...i18n import _


def execute_include_action(
    *,
    batch_name: str,
    context: _action_context.BatchSourceActionContext,
    selection: _action_selection.BatchSourceActionSelection,
    replacement_payload: ReplacementPayload | None,
) -> None:
    """Include selected batch-source changes into the index and worktree."""
    files = selection.files
    selected_ids = selection.selected_ids
    selection_ids_to_include = selection.selection_ids
    rendered = selection.rendered
    operation_parts = list(selection.operation_parts)
    failed_files = []
    candidate_counts = {}
    include_plans = []
    mode_actions = []

    for file_path, file_meta in files.items():
        try:
            if _file_mode_actions.is_file_mode_action(file_meta):
                mode_actions.append((file_path, file_meta))
                continue
            if file_meta.get("file_type") == "binary":
                batch_buffer = read_binary_file_from_batch(
                    batch_name,
                    file_path,
                    file_meta,
                    missing_content_message=(
                        f"Binary file not found in batch commit: {file_path}"
                    ),
                )
                include_plans.append(
                    _action_plans.BinaryFileActionPlan(
                        file_path,
                        file_meta,
                        batch_buffer,
                    )
                )
                continue
            if is_batch_submodule_pointer(file_meta):
                include_plans.append(
                    _action_plans.SubmodulePointerActionPlan(file_path, file_meta)
                )
                continue

            try:
                text_plan_result = (
                    _text_plan_builders.build_include_text_file_action_plan(
                        file_path=file_path,
                        file_meta=file_meta,
                        selected_ids=selected_ids,
                        selection_ids_to_include=selection_ids_to_include,
                        replacement_payload=replacement_payload,
                    )
                )
            except AtomicUnitError as e:
                if rendered:
                    _atomic_unit_refusals.translate_atomic_unit_error_to_gutter_ids(
                        e,
                        rendered,
                        "include from",
                        batch_name,
                    )
                _action_plans.close_action_plans(include_plans)
                exit_with_error(
                    _("Failed to include from batch '{name}': {error}").format(
                        name=batch_name,
                        error=str(e),
                    )
                )
            except ValueError as e:
                _action_plans.close_action_plans(include_plans)
                exit_with_error(str(e))

            if text_plan_result.missing_source:
                failed_files.append(file_path)
                continue
            if text_plan_result.plan is None:
                continue
            include_plans.append(text_plan_result.plan)

        except MergeError:
            candidate_count = (
                _candidate_preview_counts.count_include_candidate_previews_for_file(
                    batch_name=batch_name,
                    file_path=file_path,
                    file_meta=file_meta,
                    selection_ids_to_include=selection_ids_to_include,
                    replacement_payload=replacement_payload,
                )
            )
            if candidate_count.count or candidate_count.too_many or candidate_count.error:
                candidate_counts[file_path] = candidate_count
            failed_files.append(file_path)
        except CommandError:
            _action_plans.close_action_plans(include_plans)
            raise
        except Exception as e:
            print(
                _("Error staging {file}: {error}").format(
                    file=file_path,
                    error=str(e),
                ),
                file=sys.stderr,
            )
            failed_files.append(file_path)

    if failed_files:
        _action_plans.close_action_plans(include_plans)
        _candidate_refusals.refuse_candidate_conflicts(
            batch_name=batch_name,
            operation="include",
            failed_files=failed_files,
            candidate_counts=candidate_counts,
        )
        _merge_refusals.refuse_batch_source_merge_failures(
            batch_name=batch_name,
            failed_files=failed_files,
        )

    try:
        try:
            with undo_checkpoint(
                " ".join(operation_parts),
                worktree_paths=list(files),
                rollback_on_error=True,
            ):
                for plan in include_plans:
                    snapshot_file_if_untracked(plan.file_path)
                    if isinstance(plan, _action_plans.IncludeTextFileActionPlan):
                        _text_file_actions.stage_text_file_to_index(
                            plan.file_path,
                            plan.index_buffer,
                            plan.index_file_mode,
                            plan.index_change_type,
                        )
                        _text_file_actions.write_text_file_to_worktree(
                            plan.file_path,
                            plan.working_buffer,
                            plan.working_file_mode,
                            plan.working_change_type,
                        )
                    elif isinstance(plan, _action_plans.BinaryFileActionPlan):
                        _binary_file_actions.stage_binary_file_to_index(
                            plan.file_path,
                            plan.file_meta,
                            plan.buffer,
                        )
                        _binary_file_actions.write_binary_file_to_worktree(
                            plan.file_path,
                            plan.file_meta,
                            plan.buffer,
                        )
                    else:
                        stage_submodule_pointer_from_batch(
                            plan.file_path,
                            plan.file_meta,
                        )
                for file_path, file_meta in mode_actions:
                    _file_mode_actions.stage_file_mode(file_path, file_meta)
                    _file_mode_actions.apply_new_file_mode(file_path, file_meta)
        except CommandError:
            raise
        except Exception as error:
            _worktree_refusals.refuse_incompatible_worktree_action(
                batch_name=batch_name,
                file_paths=files,
                error=error,
            )
    finally:
        _action_plans.close_action_plans(include_plans)

    _action_completion.finish_batch_source_action_review(context, files)
