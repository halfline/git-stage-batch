"""Artifact-backed text planning for batch-source actions."""

from __future__ import annotations

from dataclasses import dataclass
import pickle
from pathlib import Path
from typing import Literal

from . import candidate_preview_counts as _candidate_preview_counts
from . import text_plan_builders as _text_plan_builders
from ...core.buffer import write_buffer_to_path
from ...core.replacement import ReplacementPayload
from ...data.file_target_identity import IndexIdentity, WorktreeIdentity
from ...exceptions import AtomicUnitError, CommandError, MergeError


ApplyTextPlanOutcome = Literal[
    "plan",
    "noop",
    "missing_source",
    "merge_error",
    "atomic_unit_error",
    "command_error",
    "unexpected_error",
]
IncludeTextPlanOutcome = Literal[
    "plan",
    "noop",
    "missing_source",
    "merge_error",
    "atomic_unit_error",
    "command_error",
    "value_error",
    "unexpected_error",
]
_APPLY_DETAIL_OUTCOMES = frozenset({
    "merge_error",
    "atomic_unit_error",
    "command_error",
    "unexpected_error",
})
_INCLUDE_DETAIL_OUTCOMES = _APPLY_DETAIL_OUTCOMES | {"value_error"}
_EMPTY_OUTCOMES = frozenset({
    "noop",
    "missing_source",
})
_TEXT_CHANGE_TYPES = frozenset({
    "added",
    "modified",
    "deleted",
})


@dataclass(frozen=True, slots=True)
class ApplyTextPlanJob:
    """Compact worker request for one text apply plan."""

    ordinal: int
    file_path: str
    input_artifact_path: str
    output_path: str
    details_artifact_path: str
    expected_worktree_identity: WorktreeIdentity


@dataclass(frozen=True, slots=True)
class ApplyTextPlanJobResult:
    """Compact worker result for one text apply plan."""

    ordinal: int
    file_path: str
    outcome: ApplyTextPlanOutcome
    details_artifact_path: str | None
    output_path: str | None
    file_mode: str | None
    change_type: str | None


@dataclass(frozen=True, slots=True)
class IncludeTextPlanJob:
    """Compact worker request for one text include plan."""

    ordinal: int
    file_path: str
    input_artifact_path: str
    index_output_path: str
    worktree_output_path: str
    details_artifact_path: str
    expected_index_identity: IndexIdentity
    expected_worktree_identity: WorktreeIdentity


@dataclass(frozen=True, slots=True)
class IncludeTextPlanJobResult:
    """Compact worker result for one text include plan."""

    ordinal: int
    file_path: str
    outcome: IncludeTextPlanOutcome
    details_artifact_path: str | None
    index_output_path: str | None
    worktree_output_path: str | None
    index_file_mode: str | None
    worktree_file_mode: str | None
    index_change_type: str | None
    worktree_change_type: str | None


def compute_apply_text_plan_job(
    job: ApplyTextPlanJob,
) -> ApplyTextPlanJobResult:
    """Build one text apply plan from immutable artifact inputs."""
    input_metadata = _read_pickle(job.input_artifact_path)
    if type(input_metadata) is not dict:
        raise TypeError("apply text-plan input must be a dictionary")
    file_meta = input_metadata["file_meta"]
    selected_ids = _optional_int_set(input_metadata["selected_ids"])
    selection_ids = _optional_int_set(input_metadata["selection_ids"])
    working_tree_artifact_path = input_metadata["working_tree_artifact_path"]
    scratch_directory = input_metadata["scratch_directory"]
    batch_name = input_metadata["batch_name"]
    batch_source_object_id = input_metadata["batch_source_object_id"]
    batch_source_required = apply_text_plan_requires_source(
        file_meta,
        selected_ids,
    )
    working_tree_exists = job.expected_worktree_identity.exists
    if batch_source_required and batch_source_object_id is None:
        return _result(job, "missing_source")

    try:
        build_result = _text_plan_builders.build_apply_text_file_action_plan(
            file_path=job.file_path,
            file_meta=file_meta,
            selected_ids=selected_ids,
            selection_ids_to_apply=selection_ids,
            batch_source_object_id=batch_source_object_id,
            working_tree_artifact_path=working_tree_artifact_path,
            captured_working_tree_exists=working_tree_exists,
            spool_dir=scratch_directory,
        )
        if build_result.missing_source:
            return _result(job, "missing_source")
        if build_result.plan is None:
            return _result(job, "noop")

        plan = build_result.plan
        try:
            change_type = getattr(plan.change_type, "value", plan.change_type)
            output_path = None
            if plan.buffer is not None and change_type != "deleted":
                write_buffer_to_path(job.output_path, plan.buffer)
                output_path = job.output_path
            return _result(
                job,
                "plan",
                output_path=output_path,
                file_mode=plan.file_mode,
                change_type=change_type,
            )
        finally:
            plan.close()
    except AtomicUnitError as error:
        _write_pickle(
            job.details_artifact_path,
            {
                "message": str(error),
                "required_selection_ids": (
                    None
                    if error.required_selection_ids is None
                    else sorted(error.required_selection_ids)
                ),
                "unit_kind": error.unit_kind,
            },
        )
        return _result(job, "atomic_unit_error", has_details=True)
    except MergeError:
        candidate_count = (
            _candidate_preview_counts.count_apply_candidate_previews_for_file(
                batch_name=batch_name,
                file_path=job.file_path,
                file_meta=file_meta,
                selection_ids_to_apply=selection_ids,
                batch_source_object_id=batch_source_object_id,
                working_tree_artifact_path=working_tree_artifact_path,
                captured_working_tree_exists=working_tree_exists,
                spool_dir=scratch_directory,
            )
        )
        _write_pickle(
            job.details_artifact_path,
            {
                "candidate_count": candidate_count.count,
                "candidate_too_many": candidate_count.too_many,
                "candidate_error": candidate_count.error,
            },
        )
        return _result(job, "merge_error", has_details=True)
    except CommandError as error:
        _write_pickle(
            job.details_artifact_path,
            {
                "message": error.message,
                "exit_code": error.exit_code,
            },
        )
        return _result(job, "command_error", has_details=True)
    except Exception as error:
        _write_pickle(
            job.details_artifact_path,
            {
                "message": str(error),
                "error_type": type(error).__name__,
            },
        )
        return _result(job, "unexpected_error", has_details=True)


def compute_include_text_plan_job(
    job: IncludeTextPlanJob,
) -> IncludeTextPlanJobResult:
    """Build one text include plan from immutable artifact inputs."""
    input_metadata = _read_pickle(job.input_artifact_path)
    if type(input_metadata) is not dict:
        raise TypeError("include text-plan input must be a dictionary")
    file_meta = input_metadata["file_meta"]
    selected_ids = _optional_int_set(input_metadata["selected_ids"])
    selection_ids = _optional_int_set(input_metadata["selection_ids"])
    working_tree_artifact_path = input_metadata["working_tree_artifact_path"]
    replacement_payload = _replacement_payload_from_metadata(input_metadata)
    scratch_directory = input_metadata["scratch_directory"]
    batch_name = input_metadata["batch_name"]
    batch_source_object_id = input_metadata["batch_source_object_id"]
    source_required = include_text_plan_requires_source(
        file_meta,
        selected_ids,
    )
    if source_required and batch_source_object_id is None:
        return _include_result(job, "missing_source")

    try:
        build_result = _text_plan_builders.build_include_text_file_action_plan(
            file_path=job.file_path,
            file_meta=file_meta,
            selected_ids=selected_ids,
            selection_ids_to_include=selection_ids,
            replacement_payload=replacement_payload,
            batch_source_object_id=batch_source_object_id,
            captured_index_identity=job.expected_index_identity,
            working_tree_artifact_path=working_tree_artifact_path,
            captured_working_tree_exists=job.expected_worktree_identity.exists,
            spool_dir=scratch_directory,
        )
        if build_result.missing_source:
            return _include_result(job, "missing_source")
        if build_result.plan is None:
            return _include_result(job, "noop")

        plan = build_result.plan
        try:
            index_change_type = getattr(
                plan.index_change_type,
                "value",
                plan.index_change_type,
            )
            worktree_change_type = getattr(
                plan.working_change_type,
                "value",
                plan.working_change_type,
            )
            index_output_path = _write_plan_output(
                job.index_output_path,
                plan.index_buffer,
                index_change_type,
            )
            worktree_output_path = _write_plan_output(
                job.worktree_output_path,
                plan.working_buffer,
                worktree_change_type,
            )
            return _include_result(
                job,
                "plan",
                index_output_path=index_output_path,
                worktree_output_path=worktree_output_path,
                index_file_mode=plan.index_file_mode,
                worktree_file_mode=plan.working_file_mode,
                index_change_type=index_change_type,
                worktree_change_type=worktree_change_type,
            )
        finally:
            plan.close()
    except AtomicUnitError as error:
        _write_pickle(
            job.details_artifact_path,
            {
                "message": str(error),
                "required_selection_ids": (
                    None
                    if error.required_selection_ids is None
                    else sorted(error.required_selection_ids)
                ),
                "unit_kind": error.unit_kind,
            },
        )
        return _include_result(
            job,
            "atomic_unit_error",
            has_details=True,
        )
    except MergeError:
        candidate_count = (
            _candidate_preview_counts.count_include_candidate_previews_for_file(
                batch_name=batch_name,
                file_path=job.file_path,
                file_meta=file_meta,
                selection_ids_to_include=selection_ids,
                replacement_payload=replacement_payload,
                batch_source_object_id=batch_source_object_id,
                captured_index_identity=job.expected_index_identity,
                working_tree_artifact_path=working_tree_artifact_path,
                captured_working_tree_exists=(
                    job.expected_worktree_identity.exists
                ),
                spool_dir=scratch_directory,
            )
        )
        _write_pickle(
            job.details_artifact_path,
            {
                "candidate_count": candidate_count.count,
                "candidate_too_many": candidate_count.too_many,
                "candidate_error": candidate_count.error,
            },
        )
        return _include_result(job, "merge_error", has_details=True)
    except CommandError as error:
        _write_pickle(
            job.details_artifact_path,
            {
                "message": error.message,
                "exit_code": error.exit_code,
            },
        )
        return _include_result(job, "command_error", has_details=True)
    except ValueError as error:
        _write_pickle(
            job.details_artifact_path,
            {"message": str(error)},
        )
        return _include_result(job, "value_error", has_details=True)
    except Exception as error:
        _write_pickle(
            job.details_artifact_path,
            {
                "message": str(error),
                "error_type": type(error).__name__,
            },
        )
        return _include_result(job, "unexpected_error", has_details=True)


def apply_text_plan_requires_source(
    file_meta: dict,
    selected_ids: set[int] | None,
) -> bool:
    """Return whether one apply text plan needs batch source content."""
    return _text_plan_builders.apply_text_plan_requires_source(
        file_meta,
        selected_ids,
    )


def include_text_plan_requires_source(
    file_meta: dict,
    selected_ids: set[int] | None,
) -> bool:
    """Return whether one include text plan needs batch source content."""
    return _text_plan_builders.include_text_plan_requires_source(
        file_meta,
        selected_ids,
    )


def validate_apply_text_plan_job_result(
    job: ApplyTextPlanJob,
    result: ApplyTextPlanJobResult,
) -> None:
    """Validate one worker result before the parent opens artifacts or mutates."""
    if not isinstance(result, ApplyTextPlanJobResult):
        raise TypeError("apply text-plan worker returned an invalid result")
    if result.ordinal != job.ordinal or result.file_path != job.file_path:
        raise ValueError(
            f"apply text-plan worker returned a mismatched result for "
            f"{job.file_path}"
        )
    if result.file_mode is not None and not isinstance(result.file_mode, str):
        raise TypeError("apply text-plan result file mode must be text")
    if result.change_type is not None and not isinstance(
        result.change_type,
        str,
    ):
        raise TypeError("apply text-plan result change type must be text")

    if result.outcome == "plan":
        if result.details_artifact_path is not None:
            raise ValueError("successful apply text plan returned error details")
        if result.change_type is None:
            raise ValueError("successful apply text plan omitted its change type")
        if result.change_type not in _TEXT_CHANGE_TYPES:
            raise ValueError(
                "successful apply text plan returned an invalid change type"
            )
        expected_output_path = (
            None
            if result.change_type == "deleted"
            else job.output_path
        )
        if result.output_path != expected_output_path:
            raise ValueError(
                f"apply text-plan worker returned an invalid output path for "
                f"{job.file_path}"
            )
        return

    if result.outcome in _APPLY_DETAIL_OUTCOMES:
        if result.details_artifact_path != job.details_artifact_path:
            raise ValueError(
                f"apply text-plan worker returned an invalid details path for "
                f"{job.file_path}"
            )
    elif result.outcome in _EMPTY_OUTCOMES:
        if result.details_artifact_path is not None:
            raise ValueError(
                f"apply text-plan worker returned unexpected details for "
                f"{job.file_path}"
            )
    else:
        raise ValueError(
            f"apply text-plan worker returned an unknown outcome for "
            f"{job.file_path}"
        )

    if (
        result.output_path is not None
        or result.file_mode is not None
        or result.change_type is not None
    ):
        raise ValueError(
            f"apply text-plan worker returned plan fields for "
            f"{job.file_path} without a plan"
        )


def validate_include_text_plan_job_result(
    job: IncludeTextPlanJob,
    result: IncludeTextPlanJobResult,
) -> None:
    """Validate one include worker result before opening its artifacts."""
    if not isinstance(result, IncludeTextPlanJobResult):
        raise TypeError("include text-plan worker returned an invalid result")
    if result.ordinal != job.ordinal or result.file_path != job.file_path:
        raise ValueError(
            f"include text-plan worker returned a mismatched result for "
            f"{job.file_path}"
        )
    scalar_fields = (
        ("index file mode", result.index_file_mode),
        ("worktree file mode", result.worktree_file_mode),
        ("index change type", result.index_change_type),
        ("worktree change type", result.worktree_change_type),
    )
    for label, value in scalar_fields:
        if value is not None and not isinstance(value, str):
            raise TypeError(f"include text-plan result {label} must be text")

    if result.outcome == "plan":
        if result.details_artifact_path is not None:
            raise ValueError("successful include text plan returned error details")
        for target, change_type, output_path, expected_path in (
            (
                "index",
                result.index_change_type,
                result.index_output_path,
                job.index_output_path,
            ),
            (
                "worktree",
                result.worktree_change_type,
                result.worktree_output_path,
                job.worktree_output_path,
            ),
        ):
            if change_type not in _TEXT_CHANGE_TYPES:
                raise ValueError(
                    f"successful include text plan returned an invalid "
                    f"{target} change type"
                )
            required_path = (
                None if change_type == "deleted" else expected_path
            )
            if output_path != required_path:
                raise ValueError(
                    f"include text-plan worker returned an invalid "
                    f"{target} output path for {job.file_path}"
                )
        return

    if result.outcome in _INCLUDE_DETAIL_OUTCOMES:
        if result.details_artifact_path != job.details_artifact_path:
            raise ValueError(
                f"include text-plan worker returned an invalid details path "
                f"for {job.file_path}"
            )
    elif result.outcome in _EMPTY_OUTCOMES:
        if result.details_artifact_path is not None:
            raise ValueError(
                f"include text-plan worker returned unexpected details for "
                f"{job.file_path}"
            )
    else:
        raise ValueError(
            f"include text-plan worker returned an unknown outcome for "
            f"{job.file_path}"
        )
    if any(
        value is not None
        for value in (
            result.index_output_path,
            result.worktree_output_path,
            result.index_file_mode,
            result.worktree_file_mode,
            result.index_change_type,
            result.worktree_change_type,
        )
    ):
        raise ValueError(
            f"include text-plan worker returned plan fields for "
            f"{job.file_path} without a plan"
        )


def _result(
    job: ApplyTextPlanJob,
    outcome: ApplyTextPlanOutcome,
    *,
    has_details: bool = False,
    output_path: str | None = None,
    file_mode: str | None = None,
    change_type: str | None = None,
) -> ApplyTextPlanJobResult:
    return ApplyTextPlanJobResult(
        ordinal=job.ordinal,
        file_path=job.file_path,
        outcome=outcome,
        details_artifact_path=(
            job.details_artifact_path if has_details else None
        ),
        output_path=output_path,
        file_mode=file_mode,
        change_type=change_type,
    )


def _include_result(
    job: IncludeTextPlanJob,
    outcome: IncludeTextPlanOutcome,
    *,
    has_details: bool = False,
    index_output_path: str | None = None,
    worktree_output_path: str | None = None,
    index_file_mode: str | None = None,
    worktree_file_mode: str | None = None,
    index_change_type: str | None = None,
    worktree_change_type: str | None = None,
) -> IncludeTextPlanJobResult:
    return IncludeTextPlanJobResult(
        ordinal=job.ordinal,
        file_path=job.file_path,
        outcome=outcome,
        details_artifact_path=(
            job.details_artifact_path if has_details else None
        ),
        index_output_path=index_output_path,
        worktree_output_path=worktree_output_path,
        index_file_mode=index_file_mode,
        worktree_file_mode=worktree_file_mode,
        index_change_type=index_change_type,
        worktree_change_type=worktree_change_type,
    )


def _write_plan_output(
    path: str,
    buffer,
    change_type: str,
) -> str | None:
    if buffer is None or change_type == "deleted":
        return None
    write_buffer_to_path(path, buffer)
    return path


def _replacement_payload_from_metadata(
    input_metadata: dict,
) -> ReplacementPayload | None:
    replacement_path = input_metadata["replacement_artifact_path"]
    if replacement_path is None:
        return None
    with Path(replacement_path).open("rb") as source:
        data = source.read()
    return ReplacementPayload(
        data=data,
        display_text=input_metadata["replacement_display_text"],
        exact=input_metadata["replacement_exact"],
    )


def _optional_int_set(value: object) -> set[int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise TypeError("selected identifiers must be a collection")
    if any(type(item) is not int for item in value):
        raise TypeError("selected identifiers must be integers")
    return set(value)


def _read_pickle(path: str | Path):
    with Path(path).open("rb") as source:
        return pickle.load(source)


def _write_pickle(path: str | Path, value: object) -> None:
    with Path(path).open("xb") as output:
        pickle.dump(value, output, protocol=pickle.HIGHEST_PROTOCOL)
