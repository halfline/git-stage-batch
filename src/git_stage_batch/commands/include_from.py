"""Include from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from .batch_source import action_context as _action_context
from .batch_source import action_plans as _action_plans
from .batch_source import binary_file_actions as _binary_file_actions
from .batch_source import candidate_materialization as _candidate_materialization
from .batch_source import candidate_preview_counts as _candidate_preview_counts
from .batch_source import candidate_refusals as _candidate_refusals
from .batch_source import candidate_selectors as _candidate_selectors
from .batch_source import merge_refusals as _merge_refusals
from .batch_source import text_plan_builders as _text_plan_builders
from .batch_source import text_file_actions as _text_file_actions
from .selection import replacement_selection
from ..batch.binary_file_content import read_binary_file_from_batch
from ..batch.operation_candidates import (
    clear_candidate_preview_state_for_file,
)
from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
)
from ..batch.selection import (
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.submodule_pointer import (
    is_batch_submodule_pointer,
    refuse_batch_submodule_pointer_lines,
    stage_submodule_pointer_from_batch,
)
from ..data.file_review.records import FileReviewAction
from ..data.file_review.state import (
    finish_review_scoped_line_action,
)
from ..data.file_review.batch_selection import translate_batch_file_gutter_ids_to_selection_ids
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import (
    AtomicUnitError,
    CommandError,
    MergeError,
    exit_with_error,
)
from ..i18n import _
from ..utils.git import (
    git_refresh_index,
    require_git_repository,
)


def _execute_include_candidate(
    *,
    batch_name: str,
    raw_selector: str,
    ordinal: int,
    files: dict,
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
        print(f"  {file_path}:", file=sys.stderr)
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


def command_include_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    replacement_text: Optional[str | ReplacementPayload] = None,
) -> None:
    """Stage batch changes to index and working tree using structural merge.

    Args:
        batch_name: Name of batch to include from
        line_ids: Optional line IDs to include (requires single-file context)
        file: Optional file path to select from batch.
              If None, includes all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        replacement_text: Optional replacement text for selected batch lines.
    """
    require_git_repository()
    raw_selector = batch_name
    context = _action_context.resolve_batch_source_action_context(
        raw_selector,
        operation="include",
        review_action=FileReviewAction.INCLUDE_FROM_BATCH,
        command_name="include",
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    selector = context.selector
    batch_name = context.batch_name
    scope_resolution = context.scope_resolution
    file = context.file
    all_files = context.all_files

    # Refresh index to ensure git's cached stat info is up-to-date
    git_refresh_index(check=False)

    file = resolve_current_batch_binary_file_scope(batch_name, all_files, file, patterns, line_ids)

    # Determine which files to operate on
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "include"
    )
    replacement_payload = (
        coerce_replacement_payload(replacement_text)
        if replacement_text is not None
        else None
    )

    if replacement_payload is not None and not selected_ids:
        exit_with_error(_("`include --from --as` requires `--line`."))

    if selected_ids:
        file_path_for_check = list(files.keys())[0]  # Single file context enforced above
        if files[file_path_for_check].get("file_type") == "binary":
            exit_with_error(_("Cannot use --lines with binary files. Include the whole file instead."))
        if is_batch_submodule_pointer(files[file_path_for_check]):
            refuse_batch_submodule_pointer_lines(_("Include"))

    # Translate gutter IDs to selection IDs if line selection is active
    selection_ids_to_include = selected_ids
    rendered = None  # Store for error translation
    if selected_ids:
        if replacement_payload is not None:
            replacement_selection.require_contiguous_display_selection(selected_ids)
        file_path_for_render = list(files.keys())[0]  # Single file context enforced above
        selection_ids_to_include, rendered = translate_batch_file_gutter_ids_to_selection_ids(
            batch_name,
            file_path_for_render,
            selected_ids,
            FileReviewAction.INCLUDE_FROM_BATCH,
        )
    operation_parts = ["include", "--from", raw_selector]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    if replacement_payload is not None:
        operation_parts.extend(["--as", replacement_payload.display_text or "<stdin>"])
    if selector.candidate_ordinal is not None:
        _execute_include_candidate(
            batch_name=batch_name,
            raw_selector=raw_selector,
            ordinal=selector.candidate_ordinal,
            files=files,
            selected_ids=selected_ids,
            selection_ids_to_include=selection_ids_to_include,
            replacement_payload=replacement_payload,
        )
        return

    failed_files = []
    candidate_counts = {}
    include_plans = []

    for file_path, file_meta in files.items():
        try:
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
                    translate_atomic_unit_error_to_gutter_ids(
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
            # Merge conflict - batch created from different file version
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
            # Re-raise user errors (e.g., partial atomic selection)
            _action_plans.close_action_plans(include_plans)
            raise
        except Exception as e:
            print(_("Error staging {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
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
            with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
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
                        stage_submodule_pointer_from_batch(plan.file_path, plan.file_meta)
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
        _action_plans.close_action_plans(include_plans)

    for file_path in files:
        finish_review_scoped_line_action(scope_resolution.review_state, file_path=file_path)

    if replacement_payload is not None and line_ids:
        print(
            _("✓ Staged selected lines as replacement from batch '{name}'").format(name=batch_name),
            file=sys.stderr,
        )
    elif line_ids:
        print(_("✓ Staged selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Staged changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Staged changes from batch '{name}'").format(name=batch_name), file=sys.stderr)
