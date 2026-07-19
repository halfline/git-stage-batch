"""Include-from execution for batch-source action commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from . import action_completion as _action_completion
from . import action_context as _action_context
from . import action_plans as _action_plans
from . import action_selection as _action_selection
from . import atomic_unit_refusals as _atomic_unit_refusals
from . import binary_file_actions as _binary_file_actions
from . import candidate_refusals as _candidate_refusals
from . import file_mode_actions as _file_mode_actions
from . import merge_refusals as _merge_refusals
from . import text_file_actions as _text_file_actions
from . import text_plan_jobs as _text_plan_jobs
from . import worktree_refusals as _worktree_refusals
from ...batch.binary_file_content import read_binary_file_from_batch
from ...batch.operation_candidate_types import CandidatePreviewCount
from ...batch.submodule_pointer import (
    is_batch_submodule_pointer,
    stage_submodule_pointer_from_batch,
)
from ...core.replacement import ReplacementPayload
from ...data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identity,
    index_identity_from_entry,
)
from ...data.index_entries import read_index_entries
from ...data.session import snapshot_file_if_untracked
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import AtomicUnitError, CommandError, exit_with_error
from ...i18n import _
from ...utils.file_job_workspace import FileJobWorkspace
from ...utils.file_jobs import (
    OrderedFileJob,
    run_file_jobs,
    run_validated_file_jobs,
)
from ...utils.git_object_io import list_git_tree_blobs, resolve_git_objects
from ...utils.git_repository import get_git_repository_root_path


@dataclass(frozen=True, slots=True)
class _IncludeTextInput:
    ordinal: int
    file_path: str
    file_meta: dict
    index_identity: IndexIdentity
    worktree_identity: WorktreeIdentity
    worktree_artifact: Path
    scratch_directory: Path
    source_commit: str | None
    source_required: bool


@dataclass(frozen=True, slots=True)
class _IncludePlanCapture:
    text_inputs: tuple[_IncludeTextInput, ...]
    plans_by_ordinal: dict[int, _action_plans.BatchSourceActionPlan]
    command_errors_by_ordinal: dict[int, CommandError]
    unexpected_errors_by_ordinal: dict[int, str]
    mode_actions: list[tuple[str, dict]]
    binary_metadata_by_ordinal: dict[int, dict]
    index_identities: dict[str, IndexIdentity]
    worktree_identities: dict[str, WorktreeIdentity]


def execute_include_action(
    *,
    batch_name: str,
    context: _action_context.BatchSourceActionContext,
    selection: _action_selection.BatchSourceActionSelection,
    replacement_payload: ReplacementPayload | None,
) -> None:
    """Include selected batch-source changes into the index and worktree."""
    files = selection.files
    repository_root = get_git_repository_root_path()
    with FileJobWorkspace() as workspace:
        (
            include_plans,
            mode_actions,
            expected_index_identities,
            expected_worktree_identities,
        ) = _build_include_action_plans(
            batch_name=batch_name,
            files=files,
            selected_ids=selection.selected_ids,
            selection_ids_to_include=selection.selection_ids,
            rendered=selection.rendered,
            replacement_payload=replacement_payload,
            repository_root=repository_root,
            workspace=workspace,
        )
        try:
            _require_unchanged_include_targets(
                expected_index_identities,
                expected_worktree_identities,
            )
            try:
                with undo_checkpoint(
                    " ".join(selection.operation_parts),
                    worktree_paths=list(files),
                    rollback_on_error=True,
                ):
                    for plan in include_plans:
                        snapshot_file_if_untracked(plan.file_path)
                        if isinstance(
                            plan,
                            _action_plans.IncludeTextFileActionPlan,
                        ):
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
                        elif isinstance(
                            plan,
                            _action_plans.BinaryFileActionPlan,
                        ):
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
                        _file_mode_actions.apply_new_file_mode(
                            file_path,
                            file_meta,
                        )
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


def _build_include_action_plans(
    *,
    batch_name: str,
    files: dict[str, dict],
    selected_ids: set[int] | None,
    selection_ids_to_include: set[int] | None,
    rendered,
    replacement_payload: ReplacementPayload | None,
    repository_root: Path,
    workspace: FileJobWorkspace,
) -> tuple[
    list[_action_plans.BatchSourceActionPlan],
    list[tuple[str, dict]],
    dict[str, IndexIdentity],
    dict[str, WorktreeIdentity],
]:
    capture = _capture_include_plan_inputs(
        files=files,
        selected_ids=selected_ids,
        workspace=workspace,
    )
    try:
        jobs = _build_include_text_jobs(
            batch_name=batch_name,
            selected_ids=selected_ids,
            selection_ids_to_include=selection_ids_to_include,
            replacement_payload=replacement_payload,
            capture=capture,
            workspace=workspace,
        )
        text_results_by_ordinal = _run_include_text_jobs(
            jobs,
            repository_root=repository_root,
        )
        plans = _reduce_include_action_plans(
            batch_name=batch_name,
            files=files,
            rendered=rendered,
            capture=capture,
            text_results_by_ordinal=text_results_by_ordinal,
            workspace=workspace,
        )
        return (
            plans,
            capture.mode_actions,
            capture.index_identities,
            capture.worktree_identities,
        )
    except BaseException:
        _action_plans.close_action_plans(capture.plans_by_ordinal.values())
        raise


def _capture_include_plan_inputs(
    *,
    files: dict[str, dict],
    selected_ids: set[int] | None,
    workspace: FileJobWorkspace,
) -> _IncludePlanCapture:
    """Capture include targets and classify atomic and text inputs."""
    index_entries = read_index_entries(files)
    index_identities = {
        file_path: index_identity_from_entry(index_entries.get(file_path))
        for file_path in files
    }
    worktree_identities: dict[str, WorktreeIdentity] = {}
    plans_by_ordinal: dict[int, _action_plans.BatchSourceActionPlan] = {}
    command_errors_by_ordinal: dict[int, CommandError] = {}
    unexpected_errors_by_ordinal: dict[int, str] = {}
    mode_actions: list[tuple[str, dict]] = []
    binary_metadata_by_ordinal: dict[int, dict] = {}
    text_inputs: list[_IncludeTextInput] = []

    for ordinal, (file_path, file_meta) in enumerate(files.items()):
        try:
            if _file_mode_actions.is_file_mode_action(file_meta):
                worktree_identities[file_path] = capture_worktree_identity(file_path)
                mode_actions.append((file_path, file_meta))
                continue
            if file_meta.get("file_type") == "binary":
                worktree_identities[file_path] = capture_worktree_identity(file_path)
                binary_metadata_by_ordinal[ordinal] = file_meta
                continue
            if is_batch_submodule_pointer(file_meta):
                worktree_identities[file_path] = capture_worktree_identity(file_path)
                plans_by_ordinal[ordinal] = _action_plans.SubmodulePointerActionPlan(
                    file_path,
                    file_meta,
                )
                continue

            source_required = _text_plan_jobs.include_text_plan_requires_source(
                file_meta,
                selected_ids,
            )
            if source_required:
                worktree_artifact = workspace.artifact_path(
                    ordinal,
                    "worktree-input",
                )
                worktree_identity = capture_worktree_identity(
                    file_path,
                    content_artifact_path=worktree_artifact,
                )
                source_commit = file_meta["batch_source_commit"]
            else:
                worktree_identity = capture_worktree_identity(file_path)
                worktree_artifact = workspace.write_buffer(
                    ordinal,
                    "worktree-input",
                    (),
                )
                source_commit = None
            worktree_identities[file_path] = worktree_identity
            text_inputs.append(
                _IncludeTextInput(
                    ordinal=ordinal,
                    file_path=file_path,
                    file_meta=file_meta,
                    index_identity=index_identities[file_path],
                    worktree_identity=worktree_identity,
                    worktree_artifact=worktree_artifact,
                    scratch_directory=workspace.scratch_directory(ordinal),
                    source_commit=source_commit,
                    source_required=source_required,
                )
            )
        except CommandError as error:
            command_errors_by_ordinal[ordinal] = error
        except Exception as error:
            unexpected_errors_by_ordinal[ordinal] = str(error)
    return _IncludePlanCapture(
        text_inputs=tuple(text_inputs),
        plans_by_ordinal=plans_by_ordinal,
        command_errors_by_ordinal=command_errors_by_ordinal,
        unexpected_errors_by_ordinal=unexpected_errors_by_ordinal,
        mode_actions=mode_actions,
        binary_metadata_by_ordinal=binary_metadata_by_ordinal,
        index_identities=index_identities,
        worktree_identities=worktree_identities,
    )


def _build_include_text_jobs(
    *,
    batch_name: str,
    selected_ids: set[int] | None,
    selection_ids_to_include: set[int] | None,
    replacement_payload: ReplacementPayload | None,
    capture: _IncludePlanCapture,
    workspace: FileJobWorkspace,
) -> list[OrderedFileJob[_text_plan_jobs.IncludeTextPlanJob]]:
    """Build compact include text jobs from captured inputs."""
    text_inputs = capture.text_inputs
    jobs: list[OrderedFileJob[_text_plan_jobs.IncludeTextPlanJob]] = []

    source_blob_by_target = _resolve_include_text_source_blobs(text_inputs)
    object_ids = [
        *source_blob_by_target.values(),
        *(
            text_input.index_identity.content_object_id
            for text_input in text_inputs
            if (
                text_input.source_required
                and text_input.index_identity.content_object_id is not None
            )
        ),
    ]
    object_info_by_id = resolve_git_objects(object_ids)
    for text_input in text_inputs:
        ordinal = text_input.ordinal
        file_path = text_input.file_path
        source_object_id = source_blob_by_target.get((ordinal, file_path))
        try:
            replacement_artifact_path = None
            if replacement_payload is not None:
                replacement_artifact_path = workspace.write_buffer(
                    ordinal,
                    "replacement-input",
                    (replacement_payload.data,),
                )
            input_artifact = workspace.write_pickle(
                ordinal,
                "include-input.pickle",
                {
                    "batch_name": batch_name,
                    "batch_source_object_id": source_object_id,
                    "file_meta": text_input.file_meta,
                    "selected_ids": (
                        None if selected_ids is None else sorted(selected_ids)
                    ),
                    "selection_ids": (
                        None
                        if selection_ids_to_include is None
                        else sorted(selection_ids_to_include)
                    ),
                    "working_tree_artifact_path": str(text_input.worktree_artifact),
                    "replacement_artifact_path": (
                        None
                        if replacement_artifact_path is None
                        else str(replacement_artifact_path)
                    ),
                    "replacement_display_text": (
                        None
                        if replacement_payload is None
                        else replacement_payload.display_text
                    ),
                    "replacement_exact": (
                        True
                        if replacement_payload is None
                        else replacement_payload.exact
                    ),
                    "scratch_directory": str(text_input.scratch_directory),
                },
            )
            index_output_path = workspace.output_path(
                ordinal,
                "index-output",
            )
            worktree_output_path = workspace.output_path(
                ordinal,
                "worktree-output",
            )
            details_path = workspace.output_path(
                ordinal,
                "details.pickle",
            )
            payload = _text_plan_jobs.IncludeTextPlanJob(
                ordinal=ordinal,
                file_path=file_path,
                input_artifact_path=str(input_artifact),
                index_output_path=str(index_output_path),
                worktree_output_path=str(worktree_output_path),
                details_artifact_path=str(details_path),
                expected_index_identity=text_input.index_identity,
                expected_worktree_identity=text_input.worktree_identity,
            )
            source_info = object_info_by_id.get(source_object_id)
            index_info = object_info_by_id.get(
                text_input.index_identity.content_object_id
            )
            jobs.append(
                OrderedFileJob(
                    ordinal=ordinal,
                    file_path=file_path,
                    estimated_bytes=(
                        (
                            (text_input.worktree_identity.size or 0)
                            + (
                                source_info.size
                                if source_info is not None
                                and source_info.object_type == "blob"
                                else 0
                            )
                            + (
                                index_info.size
                                if index_info is not None
                                and index_info.object_type == "blob"
                                else 0
                            )
                            + (
                                0
                                if replacement_payload is None
                                else len(replacement_payload.data)
                            )
                        )
                        if text_input.source_required
                        else 0
                    ),
                    payload=payload,
                )
            )
        except CommandError as error:
            capture.command_errors_by_ordinal[ordinal] = error
        except Exception as error:
            capture.unexpected_errors_by_ordinal[ordinal] = str(error)
    return jobs


def _run_include_text_jobs(
    jobs: list[OrderedFileJob[_text_plan_jobs.IncludeTextPlanJob]],
    *,
    repository_root: Path,
) -> dict[int, _text_plan_jobs.IncludeTextPlanJobResult]:
    """Execute and validate include text jobs in stable ordinal order."""
    paired_results = run_validated_file_jobs(
        jobs,
        _text_plan_jobs.compute_include_text_plan_job,
        _text_plan_jobs.validate_include_text_plan_job_result,
        repository_root=repository_root,
        result_label="include text-plan",
        run_jobs=run_file_jobs,
    )
    return {result.ordinal: result for _job, result in paired_results}


def _reduce_include_action_plans(
    *,
    batch_name: str,
    files: dict[str, dict],
    rendered,
    capture: _IncludePlanCapture,
    text_results_by_ordinal: dict[int, _text_plan_jobs.IncludeTextPlanJobResult],
    workspace: FileJobWorkspace,
) -> list[_action_plans.BatchSourceActionPlan]:
    """Reduce atomic and text include outcomes in source-file order."""
    failed_by_ordinal = {}
    candidate_counts = {}
    plans_by_ordinal = capture.plans_by_ordinal
    for ordinal, (file_path, _file_meta) in enumerate(files.items()):
        command_error = capture.command_errors_by_ordinal.get(ordinal)
        if command_error is not None:
            raise command_error
        unexpected_error = capture.unexpected_errors_by_ordinal.get(ordinal)
        if unexpected_error is not None:
            print(
                _("Error staging {file}: {error}").format(
                    file=file_path,
                    error=unexpected_error,
                ),
                file=sys.stderr,
            )
            failed_by_ordinal[ordinal] = file_path
            continue
        binary_metadata = capture.binary_metadata_by_ordinal.get(ordinal)
        if binary_metadata is not None:
            try:
                batch_buffer = read_binary_file_from_batch(
                    batch_name,
                    file_path,
                    binary_metadata,
                    missing_content_message=(
                        f"Binary file not found in batch commit: {file_path}"
                    ),
                )
                plans_by_ordinal[ordinal] = _action_plans.BinaryFileActionPlan(
                    file_path,
                    binary_metadata,
                    batch_buffer,
                )
            except CommandError:
                raise
            except Exception as error:
                print(
                    _("Error staging {file}: {error}").format(
                        file=file_path,
                        error=str(error),
                    ),
                    file=sys.stderr,
                )
                failed_by_ordinal[ordinal] = file_path
            continue
        result = text_results_by_ordinal.get(ordinal)
        if result is None:
            continue
        details = (
            {}
            if result.details_artifact_path is None
            else workspace.read_pickle(result.details_artifact_path)
        )
        if type(details) is not dict:
            raise TypeError("include text-plan details must be a dictionary")
        if result.outcome == "plan":
            index_buffer = (
                None
                if result.index_output_path is None
                else workspace.read_buffer(
                    result.index_output_path,
                    spool_dir=workspace.scratch_directory(result.ordinal),
                )
            )
            try:
                worktree_buffer = (
                    None
                    if result.worktree_output_path is None
                    else workspace.read_buffer(
                        result.worktree_output_path,
                        spool_dir=workspace.scratch_directory(result.ordinal),
                    )
                )
            except BaseException:
                if index_buffer is not None:
                    index_buffer.close()
                raise
            plans_by_ordinal[result.ordinal] = _action_plans.IncludeTextFileActionPlan(
                result.file_path,
                index_buffer,
                worktree_buffer,
                result.index_file_mode,
                result.worktree_file_mode,
                result.index_change_type,
                result.worktree_change_type,
            )
        elif result.outcome == "noop":
            continue
        elif result.outcome == "atomic_unit_error":
            error = AtomicUnitError(
                details["message"],
                details.get("required_selection_ids"),
                details.get("unit_kind"),
            )
            if rendered:
                _atomic_unit_refusals.translate_atomic_unit_error_to_gutter_ids(
                    error,
                    rendered,
                    "include from",
                    batch_name,
                )
            exit_with_error(
                _("Failed to include from batch '{name}': {error}").format(
                    name=batch_name,
                    error=str(error),
                )
            )
        elif result.outcome == "command_error":
            raise CommandError(
                details["message"],
                details.get("exit_code", 1),
            )
        elif result.outcome == "value_error":
            exit_with_error(details["message"])
        elif result.outcome == "unexpected_error":
            print(
                _("Error staging {file}: {error}").format(
                    file=result.file_path,
                    error=details["message"],
                ),
                file=sys.stderr,
            )
            failed_by_ordinal[ordinal] = result.file_path
        elif result.outcome == "merge_error":
            _require_unchanged_include_target(
                result.file_path,
                capture.index_identities[result.file_path],
                capture.worktree_identities[result.file_path],
            )
            candidate_count = CandidatePreviewCount(
                count=details.get("candidate_count", 0),
                too_many=details.get("candidate_too_many", False),
                error=details.get("candidate_error"),
            )
            if (
                candidate_count.count
                or candidate_count.too_many
                or candidate_count.error
            ):
                candidate_counts[result.file_path] = candidate_count
            failed_by_ordinal[ordinal] = result.file_path
        elif result.outcome == "missing_source":
            failed_by_ordinal[ordinal] = result.file_path
        else:
            raise RuntimeError(f"Unhandled include text-plan outcome: {result.outcome}")

    plans = [plans_by_ordinal[ordinal] for ordinal in sorted(plans_by_ordinal)]
    failed_files = [failed_by_ordinal[ordinal] for ordinal in sorted(failed_by_ordinal)]
    if failed_files:
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
    return plans


def _resolve_include_text_source_blobs(
    text_inputs: tuple[_IncludeTextInput, ...],
) -> dict[tuple[int, str], str]:
    paths_by_commit: dict[str, list[str]] = {}
    for text_input in text_inputs:
        if text_input.source_commit is None:
            continue
        paths_by_commit.setdefault(
            text_input.source_commit,
            [],
        ).append(text_input.file_path)
    entries_by_commit = {
        source_commit: list_git_tree_blobs(source_commit, file_paths)
        for source_commit, file_paths in paths_by_commit.items()
    }
    source_blob_by_target = {}
    for text_input in text_inputs:
        if text_input.source_commit is None:
            continue
        entry = entries_by_commit[text_input.source_commit].get(text_input.file_path)
        if entry is not None:
            source_blob_by_target[(text_input.ordinal, text_input.file_path)] = (
                entry.blob_sha
            )
    return source_blob_by_target


def _include_target_changed_error(
    file_path: str,
    *,
    target: str,
) -> CommandError:
    label = "Index" if target == "index" else "Working tree file"
    return CommandError(
        _(
            "{label} changed while include was being calculated: "
            "{file}. Retry the include command."
        ).format(label=label, file=file_path)
    )


def _require_unchanged_include_targets(
    expected_index_identities: dict[str, IndexIdentity],
    expected_worktree_identities: dict[str, WorktreeIdentity],
) -> None:
    file_paths = tuple(expected_index_identities)
    current_index_entries = read_index_entries(file_paths)
    for file_path in file_paths:
        current_index_identity = index_identity_from_entry(
            current_index_entries.get(file_path)
        )
        if current_index_identity != expected_index_identities[file_path]:
            raise _include_target_changed_error(
                file_path,
                target="index",
            )
        if (
            capture_worktree_identity(file_path)
            != expected_worktree_identities[file_path]
        ):
            raise _include_target_changed_error(
                file_path,
                target="worktree",
            )


def _require_unchanged_include_target(
    file_path: str,
    expected_index_identity: IndexIdentity,
    expected_worktree_identity: WorktreeIdentity,
) -> None:
    current_index_entry = read_index_entries((file_path,)).get(file_path)
    if index_identity_from_entry(current_index_entry) != expected_index_identity:
        raise _include_target_changed_error(file_path, target="index")
    if capture_worktree_identity(file_path) != expected_worktree_identity:
        raise _include_target_changed_error(file_path, target="worktree")
