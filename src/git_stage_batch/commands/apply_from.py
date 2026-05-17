"""Apply from batch command implementation."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

from ..batch.merge import merge_batch_from_line_sequences_as_buffer
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.operation_candidates import (
    CandidateEnumerationLimitError,
    build_apply_candidate_previews,
    clear_candidate_preview_state_for_file,
    load_candidate_preview_state,
)
from ..batch.query import get_batch_commit_sha
from ..batch.selection import (
    acquire_batch_ownership_for_display_ids_from_lines,
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    translate_batch_file_gutter_ids_to_selection_ids,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.submodule_pointer import (
    apply_submodule_pointer_from_batch,
    is_batch_submodule_pointer,
    refuse_batch_submodule_pointer_lines,
)
from ..batch.source_selector import (
    parse_batch_source_selector,
    require_candidate_operation,
)
from ..batch.validation import batch_exists
from ..core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_target_change_type,
)
from ..data.file_review_state import (
    FileReviewAction,
    finish_review_scoped_line_action,
    resolve_batch_source_action_scope,
)
from ..data.hunk_tracking import (
    render_batch_file_display,
)
from ..editor import (
    EditorBuffer,
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
    write_buffer_to_working_tree_path,
)
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import exit_with_error, MergeError, CommandError, AtomicUnitError, BatchMetadataError
from ..i18n import _
from ..utils.git import get_git_repository_root_path, require_git_repository


@dataclass
class _ApplyTextPlan:
    file_path: str
    buffer: EditorBuffer | None
    file_mode: str | None
    change_type: str

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass
class _ApplyBinaryPlan:
    file_path: str
    file_meta: dict
    buffer: EditorBuffer | None

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass(frozen=True)
class _ApplySubmodulePlan:
    file_path: str
    file_meta: dict

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class _ApplyCandidateCount:
    count: int = 0
    too_many: bool = False
    error: str | None = None


def _read_binary_file_from_batch(
    batch_name: str,
    file_path: str,
    file_meta: dict,
) -> EditorBuffer | None:
    """Read one binary batch target, or return None for a stored deletion."""
    batch_commit = get_batch_commit_sha(batch_name)
    if not batch_commit:
        raise RuntimeError(f"Batch commit not found for batch '{batch_name}'")

    change_type = file_meta.get("change_type", "modified")
    if change_type == "deleted":
        return None

    batch_buffer = load_git_object_as_buffer(f"{batch_commit}:{file_path}")
    if batch_buffer is None:
        raise RuntimeError(
            f"Binary file metadata for {file_path} says {change_type}, "
            "but the batch content is missing"
        )
    return batch_buffer


def _write_binary_file_from_batch(
    file_path: str,
    file_meta: dict,
    buffer: EditorBuffer | None,
) -> None:
    """Write one binary batch target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    change_type = file_meta.get("change_type", "modified")

    if change_type == "deleted":
        if os.path.lexists(full_path):
            full_path.unlink()
            print(_("✓ Deleted binary file: {file}").format(file=file_path), file=sys.stderr)
        return

    if buffer is None:
        raise RuntimeError(
            f"Binary file metadata for {file_path} says {change_type}, "
            "but the batch content is missing"
        )

    write_buffer_to_working_tree_path(
        full_path,
        buffer,
        mode=str(file_meta.get("mode", "100644")),
    )

    if change_type == "added":
        print(_("✓ Applied new binary file: {file}").format(file=file_path), file=sys.stderr)
    else:
        print(_("✓ Replaced binary file: {file}").format(file=file_path), file=sys.stderr)


def _write_text_file_from_batch(
    file_path: str,
    buffer: EditorBuffer | None,
    file_mode: str | None,
    change_type: str = "modified",
) -> None:
    """Write one text batch target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    if normalized_text_change_type(change_type) == TextFileChangeType.DELETED:
        if os.path.lexists(full_path):
            full_path.unlink()
        return

    if buffer is None:
        raise RuntimeError(f"Text file not found in batch content: {file_path}")

    write_buffer_to_working_tree_path(full_path, buffer, mode=file_mode)


def _close_apply_plans(plans) -> None:
    for plan in plans:
        plan.close()


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
    if len(files) != 1:
        exit_with_error(_("Candidate execution requires exactly one file."))
    file_path, file_meta = next(iter(files.items()))
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        exit_with_error(_("Candidate execution is only available for text batch entries."))

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Batch source content is missing for {file}.").format(file=file_path))

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    file_mode = mode_for_text_materialization(
        str(file_meta.get("mode", "100644")),
        selected_ids,
        destination_exists=working_exists,
    )
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))

    with (
        batch_source_buffer as batch_source_lines,
        load_working_tree_file_as_buffer(file_path) as working_lines,
    ):
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
        ) as ownership:
            try:
                previews = build_apply_candidate_previews(
                    batch_name=batch_name,
                    file_path=file_path,
                    source_lines=batch_source_lines,
                    ownership=ownership,
                    worktree_lines=working_lines,
                    batch_source_commit=batch_source_commit,
                    file_meta=file_meta,
                    text_change_type=text_change_type,
                    worktree_file_mode=file_mode,
                    worktree_exists=working_exists,
                    selected_ids=selected_ids,
                    selection_ids=selection_ids_to_apply,
                )
            except MergeError as e:
                exit_with_error(str(e))

            if ordinal < 1 or ordinal > len(previews):
                for preview in previews:
                    preview.close()
                exit_with_error(
                    _("Batch '{batch}' has {count} apply candidates for {file}; candidate {ordinal} does not exist.").format(
                        batch=batch_name,
                        count=len(previews),
                        file=file_path,
                        ordinal=ordinal,
                    )
                )

            preview = previews[ordinal - 1]
            try:
                state = load_candidate_preview_state(preview)
                if (
                    state is None
                    or state.get("ordinal") != ordinal
                    or state.get("candidate_id") != preview.candidate_id
                    or state.get("target_fingerprints") != preview.target_fingerprints
                    or state.get("target_result_fingerprints") != preview.target_result_fingerprints
                ):
                    exit_with_error(
                        _(
                            "Candidate selector '{selector}' has not been previewed for {file}.\n"
                            "No changes applied.\n\n"
                            "Preview it first with:\n"
                            "  git-stage-batch show --from {selector} --file {file}"
                        ).format(selector=raw_selector, file=file_path)
                    )

                target = preview.targets[0]
                effective_change_type = selected_text_target_change_type(
                    text_change_type,
                    selected_ids,
                    target.after_buffer,
                )
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
                    _write_text_file_from_batch(
                        file_path,
                        target.after_buffer,
                        file_mode,
                        effective_change_type,
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
                for candidate in previews:
                    candidate.close()


def _apply_candidate_count_for_file(
    *,
    batch_name: str,
    file_path: str,
    file_meta: dict,
    selection_ids_to_apply: set[int] | None,
) -> _ApplyCandidateCount:
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        return _ApplyCandidateCount()
    batch_source_commit = file_meta.get("batch_source_commit")
    if not batch_source_commit:
        return _ApplyCandidateCount()
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        return _ApplyCandidateCount()
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    file_mode = mode_for_text_materialization(
        str(file_meta.get("mode", "100644")),
        selection_ids_to_apply,
        destination_exists=working_exists,
    )
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    try:
        with (
            batch_source_buffer as batch_source_lines,
            load_working_tree_file_as_buffer(file_path) as working_lines,
        ):
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_apply,
            ) as ownership:
                previews = build_apply_candidate_previews(
                    batch_name=batch_name,
                    file_path=file_path,
                    source_lines=batch_source_lines,
                    ownership=ownership,
                    worktree_lines=working_lines,
                    batch_source_commit=batch_source_commit,
                    file_meta=file_meta,
                    text_change_type=text_change_type,
                    worktree_file_mode=file_mode,
                    worktree_exists=working_exists,
                    selected_ids=selection_ids_to_apply,
                    selection_ids=selection_ids_to_apply,
                )
                count = len(previews)
                for preview in previews:
                    preview.close()
                return _ApplyCandidateCount(count=count)
    except CandidateEnumerationLimitError as e:
        return _ApplyCandidateCount(too_many=True, error=str(e))
    except MergeError:
        return _ApplyCandidateCount()
    except Exception as e:
        return _ApplyCandidateCount(error=str(e))


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
    selector = parse_batch_source_selector(batch_name)
    require_candidate_operation(selector, "apply", raw_value=raw_selector, file=file)
    if selector.candidate_operation == "apply" and selector.candidate_ordinal is None:
        exit_with_error(
            _(
                "'{selector}' names the apply candidate preview set.\n"
                "Use 'git-stage-batch show --from {selector}' to preview candidates, "
                "or use '{batch}:apply:N' to apply a candidate."
            ).format(selector=raw_selector, batch=selector.batch_name)
        )
    if selector.candidate_ordinal is not None and file is None:
        exit_with_error(
            _(
                "Candidate selector '{selector}' requires --file in this implementation.\n"
                "No changes applied."
            ).format(selector=raw_selector)
        )
    batch_name = selector.batch_name
    scope_resolution = resolve_batch_source_action_scope(
        FileReviewAction.APPLY_FROM_BATCH,
        command_name="apply",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    file = scope_resolution.file

    # Check batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read and validate batch metadata
    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})

    if not all_files:
        exit_with_error(_("Batch '{name}' is empty").format(name=batch_name))

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

    repo_root = get_git_repository_root_path()
    failed_files = []
    candidate_counts: dict[str, _ApplyCandidateCount] = {}
    apply_plans = []

    for file_path, file_meta in files.items():
        try:
            # Binary files are atomic units - handle separately without ownership/merge logic
            if file_meta.get("file_type") == "binary":
                batch_buffer = _read_binary_file_from_batch(batch_name, file_path, file_meta)
                apply_plans.append(_ApplyBinaryPlan(file_path, file_meta, batch_buffer))
                continue
            if is_batch_submodule_pointer(file_meta):
                apply_plans.append(_ApplySubmodulePlan(file_path, file_meta))
                continue

            text_change_type = normalized_text_change_type(file_meta.get("change_type"))

            full_path = repo_root / file_path
            working_exists = os.path.lexists(full_path)

            file_mode = mode_for_text_materialization(
                str(file_meta.get("mode", "100644")),
                selected_ids,
                destination_exists=working_exists,
            )
            if selected_ids is None and text_change_type == TextFileChangeType.DELETED:
                apply_plans.append(
                    _ApplyTextPlan(file_path, None, file_mode, text_change_type)
                )
                continue

            batch_source_commit = file_meta["batch_source_commit"]
            batch_source_buffer = load_git_object_as_buffer(
                f"{batch_source_commit}:{file_path}"
            )
            if batch_source_buffer is None:
                failed_files.append(file_path)
                continue

            with (
                batch_source_buffer as batch_source_lines,
                load_working_tree_file_as_buffer(file_path) as working_lines,
            ):
                try:
                    with acquire_batch_ownership_for_display_ids_from_lines(
                        file_meta,
                        batch_source_lines,
                        selection_ids_to_apply,
                    ) as ownership:
                        if ownership.is_empty():
                            if selected_ids is None and text_change_type == TextFileChangeType.ADDED:
                                merged_buffer = EditorBuffer.from_bytes(b"")
                            else:
                                continue
                        else:
                            merged_buffer = merge_batch_from_line_sequences_as_buffer(
                                batch_source_lines,
                                ownership,
                                working_lines,
                            )
                except AtomicUnitError as e:
                    if rendered:
                        translate_atomic_unit_error_to_gutter_ids(e, rendered, "apply", batch_name)
                    _close_apply_plans(apply_plans)
                    exit_with_error(_("Failed to apply batch '{name}': {error}").format(
                        name=batch_name,
                        error=str(e)
                    ))

            effective_change_type = selected_text_target_change_type(
                text_change_type,
                selected_ids,
                merged_buffer,
            )
            apply_plans.append(
                _ApplyTextPlan(file_path, merged_buffer, file_mode, effective_change_type)
            )

        except MergeError:
            # Merge conflict - batch created from different file version
            candidate_count = _apply_candidate_count_for_file(
                batch_name=batch_name,
                file_path=file_path,
                file_meta=file_meta,
                selection_ids_to_apply=selection_ids_to_apply,
            )
            if candidate_count.count or candidate_count.too_many or candidate_count.error:
                candidate_counts[file_path] = candidate_count
            failed_files.append(file_path)
        except CommandError:
            # Re-raise user errors (e.g., partial atomic selection)
            _close_apply_plans(apply_plans)
            raise
        except Exception as e:
            print(_("Error applying {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
            failed_files.append(file_path)

    if failed_files:
        _close_apply_plans(apply_plans)
        candidate_limit_files = [
            file_path
            for file_path in failed_files
            if candidate_counts.get(file_path, _ApplyCandidateCount()).too_many
        ]
        if len(candidate_limit_files) == 1:
            file_path = candidate_limit_files[0]
            exit_with_error(
                _(
                    "Cannot apply batch '{batch}': {file} has too many apply "
                    "candidates to preview safely.\n"
                    "No changes applied.\n\n"
                    "Use --line with a narrower selection or split the batch "
                    "before previewing candidates."
                ).format(batch=batch_name, file=file_path)
            )
        if len(candidate_limit_files) > 1:
            exit_with_error(
                _(
                    "Cannot apply batch '{batch}': multiple files have too many "
                    "apply candidates to preview safely.\n"
                    "No changes applied.\n\n"
                    "Use --line with narrower selections or split the batch "
                    "before previewing candidates."
                ).format(batch=batch_name)
            )

        candidate_error_files = [
            file_path
            for file_path in failed_files
            if (
                candidate_counts.get(file_path, _ApplyCandidateCount()).error
                and not candidate_counts.get(file_path, _ApplyCandidateCount()).too_many
            )
        ]
        if len(candidate_error_files) == 1:
            file_path = candidate_error_files[0]
            error = candidate_counts[file_path].error
            exit_with_error(
                _(
                    "Cannot enumerate apply candidates for {file}: {error}\n"
                    "No changes applied."
                ).format(file=file_path, error=error)
            )
        if len(candidate_error_files) > 1:
            examples = "\n".join(
                f"  {file_path}: {candidate_counts[file_path].error}"
                for file_path in candidate_error_files[:3]
            )
            exit_with_error(
                _(
                    "Cannot enumerate apply candidates for multiple files.\n"
                    "No changes applied.\n\n"
                    "{examples}"
                ).format(examples=examples)
            )

        ambiguous_files = [
            file_path
            for file_path in failed_files
            if candidate_counts.get(file_path, _ApplyCandidateCount()).count
        ]
        if len(ambiguous_files) == 1:
            file_path = ambiguous_files[0]
            exit_with_error(
                _(
                    "Cannot apply batch '{batch}': {file} has {count} apply candidates.\n"
                    "No changes applied.\n\n"
                    "Preview candidates:\n"
                    "  git-stage-batch show --from {batch}:apply --file {file}\n\n"
                    "Apply a reviewed candidate:\n"
                    "  git-stage-batch apply --from {batch}:apply:N --file {file}"
                ).format(
                    batch=batch_name,
                    file=file_path,
                    count=candidate_counts[file_path].count,
                )
            )
        if len(ambiguous_files) > 1:
            examples = "\n".join(
                f"  git-stage-batch show --from {batch_name}:apply --file {file_path}"
                for file_path in ambiguous_files[:3]
            )
            exit_with_error(
                _(
                    "Cannot apply batch '{batch}': multiple files need apply decisions.\n"
                    "No changes applied.\n\n"
                    "Resolve one file at a time:\n{examples}"
                ).format(batch=batch_name, examples=examples)
            )
        if len(failed_files) == 1:
            # Check if there are individually mergeable lines to suggest --lines
            file_path = failed_files[0]
            rendered = render_batch_file_display(batch_name, file_path)
            has_mergeable_lines = rendered and len(rendered.gutter_to_selection_id) > 0

            if has_mergeable_lines:
                error_msg = _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                             "Use 'git-stage-batch show --from {batch}' to review the batch, "
                             "or use '--lines' to apply only specific changes.").format(
                    batch=batch_name,
                    file=file_path
                )
            else:
                error_msg = _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                             "Use 'git-stage-batch show --from {batch}' to review the batch.").format(
                    batch=batch_name,
                    file=file_path
                )
            exit_with_error(error_msg)
        else:
            exit_with_error(
                _("Batch '{batch}' contains changes to one or more files that are incompatible with the current working tree. "
                  "Failed for: {files}. "
                  "Use 'git-stage-batch show --from {batch}' to review the batch, "
                  "or use '--lines' to apply only specific changes.").format(
                    batch=batch_name,
                    files=', '.join(failed_files)
                )
            )

    try:
        try:
            with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
                for plan in apply_plans:
                    snapshot_file_if_untracked(plan.file_path)
                    if isinstance(plan, _ApplyTextPlan):
                        _write_text_file_from_batch(
                            plan.file_path,
                            plan.buffer,
                            plan.file_mode,
                            plan.change_type,
                        )
                    elif isinstance(plan, _ApplyBinaryPlan):
                        _write_binary_file_from_batch(
                            plan.file_path,
                            plan.file_meta,
                            plan.buffer,
                        )
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
        _close_apply_plans(apply_plans)

    for file_path in files:
        finish_review_scoped_line_action(scope_resolution.review_state, file_path=file_path)

    if line_ids:
        print(_("✓ Applied selected lines from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Applied changes for {file} from batch '{name}' to working tree").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Applied changes from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
