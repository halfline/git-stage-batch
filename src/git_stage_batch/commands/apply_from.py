"""Apply from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from .batch_source import action_context as _action_context
from .batch_source import action_plans as _action_plans
from .batch_source import binary_file_actions as _binary_file_actions
from .batch_source import candidate_materialization as _candidate_materialization
from .batch_source import candidate_preview_counts as _candidate_preview_counts
from .batch_source import candidate_refusals as _candidate_refusals
from .batch_source import merge_refusals as _merge_refusals
from .batch_source import text_file_actions as _text_file_actions
from .batch_source import text_plan_builders as _text_plan_builders
from ..batch.binary_file_content import read_binary_file_from_batch
from ..batch.operation_candidates import (
    clear_candidate_preview_state_for_file,
)
from ..batch.selection import (
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.submodule_pointer import (
    apply_submodule_pointer_from_batch,
    is_batch_submodule_pointer,
    refuse_batch_submodule_pointer_lines,
)
from ..data.file_review.records import FileReviewAction
from ..data.file_review.state import (
    finish_review_scoped_line_action,
)
from ..data.batch_file_review_selection import translate_batch_file_gutter_ids_to_selection_ids
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import exit_with_error, MergeError, CommandError, AtomicUnitError
from ..i18n import _
from ..utils.git import require_git_repository


def _print_binary_worktree_result(
    file_path: str,
    action: _binary_file_actions.BinaryWorktreeAction | None,
) -> None:
    """Print apply-from status for a binary working-tree action."""
    if action is None:
        return

    if action is _binary_file_actions.BinaryWorktreeAction.DELETED:
        print(_("✓ Deleted binary file: {file}").format(file=file_path), file=sys.stderr)
    elif action is _binary_file_actions.BinaryWorktreeAction.ADDED:
        print(_("✓ Applied new binary file: {file}").format(file=file_path), file=sys.stderr)
    else:
        print(
            _("✓ Replaced binary file: {file}").format(file=file_path),
            file=sys.stderr,
        )


def _execute_apply_candidate(
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
            _("✓ Applied candidate {ordinal} of {count} from batch '{batch}' to working tree").format(
                ordinal=preview.ordinal,
                count=preview.count,
                batch=batch_name,
            ),
            file=sys.stderr,
        )
    finally:
        materialized.close()


def command_apply_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> None:
    """Apply batch changes to working tree using structural merge.

    Args:
        batch_name: Name of batch to apply from
        line_ids: Optional line IDs to apply (requires single-file context)
        file: Optional file path to select from batch.
              If None, applies all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
    """
    require_git_repository()
    raw_selector = batch_name
    context = _action_context.resolve_batch_source_action_context(
        raw_selector,
        operation="apply",
        review_action=FileReviewAction.APPLY_FROM_BATCH,
        command_name="apply",
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    selector = context.selector
    batch_name = context.batch_name
    scope_resolution = context.scope_resolution
    file = context.file
    all_files = context.all_files

    file = resolve_current_batch_binary_file_scope(batch_name, all_files, file, patterns, line_ids)

    # Determine which files to operate on
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "apply"
    )

    # Reject line selection for binary files (binary files are atomic units)
    if selected_ids:
        file_path_for_check = list(files.keys())[0]  # Single file context enforced above
        if files[file_path_for_check].get("file_type") == "binary":
            exit_with_error(_("Cannot use --lines with binary files. Apply the whole file instead."))
        if is_batch_submodule_pointer(files[file_path_for_check]):
            refuse_batch_submodule_pointer_lines(_("Apply"))

    # Translate gutter IDs to selection IDs if line selection is active
    selection_ids_to_apply = selected_ids
    rendered = None  # Store for error translation
    if selected_ids:
        file_path_for_render = list(files.keys())[0]  # Single file context enforced above
        selection_ids_to_apply, rendered = translate_batch_file_gutter_ids_to_selection_ids(
            batch_name,
            file_path_for_render,
            selected_ids,
            FileReviewAction.APPLY_FROM_BATCH,
        )
    operation_parts = ["apply", "--from", raw_selector]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    if selector.candidate_ordinal is not None:
        _execute_apply_candidate(
            batch_name=batch_name,
            raw_selector=raw_selector,
            ordinal=selector.candidate_ordinal,
            files=files,
            selected_ids=selected_ids,
            selection_ids_to_apply=selection_ids_to_apply,
        )
        return

    failed_files = []
    candidate_counts = {}
    apply_plans = []

    for file_path, file_meta in files.items():
        try:
            # Binary files are atomic units - handle separately without ownership/merge logic
            if file_meta.get("file_type") == "binary":
                batch_buffer = read_binary_file_from_batch(
                    batch_name,
                    file_path,
                    file_meta,
                )
                apply_plans.append(
                    _action_plans.BinaryFileActionPlan(
                        file_path,
                        file_meta,
                        batch_buffer,
                    )
                )
                continue
            if is_batch_submodule_pointer(file_meta):
                apply_plans.append(
                    _action_plans.SubmodulePointerActionPlan(file_path, file_meta)
                )
                continue

            try:
                text_plan_result = (
                    _text_plan_builders.build_apply_text_file_action_plan(
                        file_path=file_path,
                        file_meta=file_meta,
                        selected_ids=selected_ids,
                        selection_ids_to_apply=selection_ids_to_apply,
                    )
                )
            except AtomicUnitError as e:
                if rendered:
                    translate_atomic_unit_error_to_gutter_ids(
                        e,
                        rendered,
                        "apply",
                        batch_name,
                    )
                _action_plans.close_action_plans(apply_plans)
                exit_with_error(_("Failed to apply batch '{name}': {error}").format(
                    name=batch_name,
                    error=str(e)
                ))

            if text_plan_result.missing_source:
                failed_files.append(file_path)
                continue
            if text_plan_result.plan is None:
                continue
            apply_plans.append(text_plan_result.plan)

        except MergeError:
            # Merge conflict - batch created from different file version
            candidate_count = (
                _candidate_preview_counts.count_apply_candidate_previews_for_file(
                    batch_name=batch_name,
                    file_path=file_path,
                    file_meta=file_meta,
                    selection_ids_to_apply=selection_ids_to_apply,
                )
            )
            if candidate_count.count or candidate_count.too_many or candidate_count.error:
                candidate_counts[file_path] = candidate_count
            failed_files.append(file_path)
        except CommandError:
            # Re-raise user errors (e.g., partial atomic selection)
            _action_plans.close_action_plans(apply_plans)
            raise
        except Exception as e:
            print(_("Error applying {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
            failed_files.append(file_path)

    if failed_files:
        _action_plans.close_action_plans(apply_plans)
        _candidate_refusals.refuse_candidate_conflicts(
            batch_name=batch_name,
            operation="apply",
            failed_files=failed_files,
            candidate_counts=candidate_counts,
        )
        _merge_refusals.refuse_batch_source_merge_failures(
            batch_name=batch_name,
            failed_files=failed_files,
        )

    try:
        try:
            with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
                for plan in apply_plans:
                    snapshot_file_if_untracked(plan.file_path)
                    if isinstance(plan, _action_plans.ApplyTextFileActionPlan):
                        _text_file_actions.write_text_file_to_worktree(
                            plan.file_path,
                            plan.buffer,
                            plan.file_mode,
                            plan.change_type,
                        )
                    elif isinstance(plan, _action_plans.BinaryFileActionPlan):
                        action = _binary_file_actions.write_binary_file_to_worktree(
                            plan.file_path,
                            plan.file_meta,
                            plan.buffer,
                            missing_content_message=(
                                f"Binary file metadata for {plan.file_path} "
                                f"says {plan.file_meta.get('change_type', 'modified')}, "
                                "but the batch content is missing"
                            ),
                        )
                        _print_binary_worktree_result(plan.file_path, action)
                    else:
                        apply_submodule_pointer_from_batch(plan.file_path, plan.file_meta)
        except CommandError:
            raise
        except Exception:
            if len(files) == 1:
                file_path = next(iter(files))
                exit_with_error(
                    _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                      "Use 'git-stage-batch show --from {batch}' to review the batch.").format(
                        batch=batch_name,
                        file=file_path,
                    )
                )
            exit_with_error(
                _("Batch '{batch}' contains changes to one or more files that are incompatible with the current working tree. "
                  "Use 'git-stage-batch show --from {batch}' to review the batch.").format(
                    batch=batch_name,
                )
            )
    finally:
        _action_plans.close_action_plans(apply_plans)

    for file_path in files:
        finish_review_scoped_line_action(scope_resolution.review_state, file_path=file_path)

    if line_ids:
        print(_("✓ Applied selected lines from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Applied changes for {file} from batch '{name}' to working tree").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Applied changes from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
