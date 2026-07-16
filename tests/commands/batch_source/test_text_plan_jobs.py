"""Tests for artifact-backed apply text planning."""

import pickle
from pathlib import Path

import pytest

from git_stage_batch.commands.batch_source.action_plans import (
    ApplyTextFileActionPlan,
)
from git_stage_batch.commands.batch_source.text_plan_builders import (
    ApplyTextPlanBuildResult,
)
from git_stage_batch.commands.batch_source.text_plan_jobs import (
    ApplyTextPlanJob,
    ApplyTextPlanJobResult,
    compute_apply_text_plan_job,
    validate_apply_text_plan_job_result,
)
import git_stage_batch.commands.batch_source.apply_action as apply_action
import git_stage_batch.commands.batch_source.text_plan_jobs as text_plan_jobs
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.data.file_target_identity import WorktreeIdentity
from git_stage_batch.exceptions import AtomicUnitError, CommandError
from git_stage_batch.utils.file_job_workspace import FileJobWorkspace
from git_stage_batch.utils.file_jobs import assert_file_job_transport_value


def _job(
    workspace,
    *,
    selected_ids=None,
    batch_source_object_id="a" * 40,
    change_type="modified",
):
    worktree_path = workspace.write_buffer(0, "worktree", (b"old\n",))
    scratch = workspace.scratch_directory(0)
    input_path = workspace.write_pickle(
        0,
        "input.pickle",
        {
            "batch_name": "batch",
            "batch_source_object_id": batch_source_object_id,
            "file_meta": {
                "batch_source_commit": "b" * 40,
                "change_type": change_type,
            },
            "selected_ids": selected_ids,
            "selection_ids": selected_ids,
            "working_tree_artifact_path": str(worktree_path),
            "scratch_directory": str(scratch),
        },
    )
    return ApplyTextPlanJob(
        ordinal=0,
        file_path="file.txt",
        input_artifact_path=str(input_path),
        output_path=str(workspace.output_path(0, "merged")),
        details_artifact_path=str(workspace.output_path(0, "details")),
        expected_worktree_identity=WorktreeIdentity(
            True,
            "regular",
            0o644,
            4,
            "digest",
        ),
    )


def test_compute_apply_keeps_value_errors_as_file_failures(
    tmp_path,
    monkeypatch,
):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        monkeypatch.setattr(
            text_plan_jobs._text_plan_builders,
            "build_apply_text_file_action_plan",
            lambda **_kwargs: (_ for _ in ()).throw(
                ValueError("invalid apply target")
            ),
        )

        result = compute_apply_text_plan_job(job)

        assert result.outcome == "unexpected_error"
        assert workspace.read_pickle(result.details_artifact_path) == {
            "message": "invalid apply target",
            "error_type": "ValueError",
        }


def test_compute_streams_plan_output_to_the_workspace(tmp_path, monkeypatch):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        captured = {}

        def build_plan(**kwargs):
            captured.update(kwargs)
            return ApplyTextPlanBuildResult(
                plan=ApplyTextFileActionPlan(
                    "file.txt",
                    LineBuffer.from_bytes(b"merged\n"),
                    "100644",
                    "modified",
                )
            )

        monkeypatch.setattr(
            text_plan_jobs._text_plan_builders,
            "build_apply_text_file_action_plan",
            build_plan,
        )

        result = compute_apply_text_plan_job(job)

        assert result.outcome == "plan"
        assert result.output_path == job.output_path
        assert result.details_artifact_path is None
        assert Path(job.output_path).read_bytes() == b"merged\n"
        assert captured["batch_source_object_id"] == "a" * 40
        assert captured["working_tree_artifact_path"].endswith("worktree")
        assert captured["spool_dir"] == str(workspace.scratch_directory(0))


def test_compute_records_atomic_refusal_details(tmp_path, monkeypatch):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace, selected_ids=[1])

        def refuse(**_kwargs):
            raise AtomicUnitError(
                "select together",
                required_selection_ids={1, 2},
                unit_kind="replacement",
            )

        monkeypatch.setattr(
            text_plan_jobs._text_plan_builders,
            "build_apply_text_file_action_plan",
            refuse,
        )

        result = compute_apply_text_plan_job(job)
        details = workspace.read_pickle(result.details_artifact_path)

        assert result.outcome == "atomic_unit_error"
        assert details == {
            "message": "select together",
            "required_selection_ids": [1, 2],
            "unit_kind": "replacement",
        }


def test_compute_allows_whole_file_deletion_without_source_content(
    tmp_path,
):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(
            workspace,
            batch_source_object_id=None,
            change_type="deleted",
        )

        result = compute_apply_text_plan_job(job)

        assert result.outcome == "plan"
        assert result.output_path is None
        assert result.change_type == "deleted"


def test_compute_does_not_publish_empty_partial_deletion_output(
    tmp_path,
    monkeypatch,
):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)

        def build_plan(**_kwargs):
            return ApplyTextPlanBuildResult(
                plan=ApplyTextFileActionPlan(
                    "file.txt",
                    LineBuffer.from_bytes(b""),
                    None,
                    "deleted",
                )
            )

        monkeypatch.setattr(
            text_plan_jobs._text_plan_builders,
            "build_apply_text_file_action_plan",
            build_plan,
        )

        result = compute_apply_text_plan_job(job)

        assert result.outcome == "plan"
        assert result.output_path is None
        assert result.change_type == "deleted"
        assert not Path(job.output_path).exists()


def test_result_validation_rejects_worker_selected_paths(tmp_path):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        result = ApplyTextPlanJobResult(
            ordinal=job.ordinal,
            file_path=job.file_path,
            outcome="plan",
            details_artifact_path=None,
            output_path=str(tmp_path / "outside"),
            file_mode="100644",
            change_type="modified",
        )

        with pytest.raises(ValueError, match="invalid output path"):
            validate_apply_text_plan_job_result(job, result)


def test_result_validation_rejects_mismatched_target(tmp_path):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        result = ApplyTextPlanJobResult(
            ordinal=job.ordinal,
            file_path="other.txt",
            outcome="noop",
            details_artifact_path=None,
            output_path=None,
            file_mode=None,
            change_type=None,
        )

        with pytest.raises(ValueError, match="mismatched result"):
            validate_apply_text_plan_job_result(job, result)


def test_result_validation_rejects_stale_apply_worker_outcome(tmp_path):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        result = ApplyTextPlanJobResult(
            ordinal=job.ordinal,
            file_path=job.file_path,
            outcome="stale",
            details_artifact_path=None,
            output_path=None,
            file_mode=None,
            change_type=None,
        )

        with pytest.raises(ValueError, match="unknown outcome"):
            validate_apply_text_plan_job_result(job, result)


def test_parent_builds_whole_file_deletion_without_resolving_source(
    tmp_path,
    monkeypatch,
):
    identity = WorktreeIdentity(
        True,
        "regular",
        0o644,
        4,
        "digest",
    )
    monkeypatch.setattr(
        apply_action,
        "capture_worktree_identity",
        lambda *args, **kwargs: identity,
    )
    monkeypatch.setattr(
        apply_action,
        "list_git_tree_blobs",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("deletion resolved a source tree")
        ),
    )

    def resolve_no_objects(object_names):
        assert list(object_names) == []
        return {}

    monkeypatch.setattr(
        apply_action,
        "resolve_git_objects",
        resolve_no_objects,
    )

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        plans, mode_actions, expected_identities = (
            apply_action._build_apply_action_plans(
                batch_name="batch",
                files={
                    "file.txt": {
                        "change_type": "deleted",
                        "mode": "100644",
                    },
                },
                selected_ids=None,
                selection_ids_to_apply=None,
                rendered=None,
                repository_root=tmp_path,
                workspace=workspace,
            )
        )
        try:
            assert len(plans) == 1
            assert plans[0].buffer is None
            assert plans[0].change_type == "deleted"
            assert mode_actions == []
            assert expected_identities == {"file.txt": identity}
        finally:
            plans[0].close()


def test_parent_reduces_earlier_text_refusal_before_reading_later_binary(
    tmp_path,
    monkeypatch,
):
    identity = WorktreeIdentity(
        False,
        "missing",
        None,
        None,
        None,
    )
    monkeypatch.setattr(
        apply_action,
        "capture_worktree_identity",
        lambda *args, **kwargs: identity,
    )
    monkeypatch.setattr(
        apply_action,
        "resolve_git_objects",
        lambda object_names: {},
    )
    binary_reads = []

    def read_binary(*args, **kwargs):
        binary_reads.append((args, kwargs))
        return None

    monkeypatch.setattr(
        apply_action,
        "read_binary_file_from_batch",
        read_binary,
    )

    def return_atomic_refusal(jobs, _compute, **_kwargs):
        payload = jobs[0].payload
        with Path(payload.details_artifact_path).open("xb") as output:
            pickle.dump(
                {
                    "message": "select together",
                    "required_selection_ids": [1, 2],
                    "unit_kind": "replacement",
                },
                output,
            )
        return [
            ApplyTextPlanJobResult(
                ordinal=payload.ordinal,
                file_path=payload.file_path,
                outcome="atomic_unit_error",
                details_artifact_path=payload.details_artifact_path,
                output_path=None,
                file_mode=None,
                change_type=None,
            )
        ]

    monkeypatch.setattr(
        apply_action,
        "run_file_jobs",
        return_atomic_refusal,
    )

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        with pytest.raises(CommandError, match="select together"):
            apply_action._build_apply_action_plans(
                batch_name="batch",
                files={
                    "first.txt": {
                        "change_type": "deleted",
                        "mode": "100644",
                    },
                    "second.bin": {
                        "file_type": "binary",
                        "change_type": "deleted",
                    },
                },
                selected_ids=None,
                selection_ids_to_apply=None,
                rendered=None,
                repository_root=tmp_path,
                workspace=workspace,
            )

    assert binary_reads == []


def test_parent_rejects_stale_merge_diagnostics(
    tmp_path,
    monkeypatch,
):
    """Candidate diagnostics should describe the target that was captured."""
    identity = WorktreeIdentity(
        True,
        "regular",
        0o644,
        4,
        "before",
    )
    stale_identity = WorktreeIdentity(
        True,
        "regular",
        0o644,
        6,
        "after",
    )
    identities = iter((identity, stale_identity))
    monkeypatch.setattr(
        apply_action,
        "capture_worktree_identity",
        lambda *args, **kwargs: next(identities),
    )
    monkeypatch.setattr(
        apply_action,
        "resolve_git_objects",
        lambda object_names: {},
    )

    def return_merge_refusal(jobs, _compute, **_kwargs):
        payload = jobs[0].payload
        with Path(payload.details_artifact_path).open("xb") as output:
            pickle.dump(
                {
                    "candidate_count": 2,
                    "candidate_too_many": False,
                    "candidate_error": None,
                },
                output,
            )
        return [
            ApplyTextPlanJobResult(
                ordinal=payload.ordinal,
                file_path=payload.file_path,
                outcome="merge_error",
                details_artifact_path=payload.details_artifact_path,
                output_path=None,
                file_mode=None,
                change_type=None,
            )
        ]

    monkeypatch.setattr(
        apply_action,
        "run_file_jobs",
        return_merge_refusal,
    )

    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        with pytest.raises(CommandError, match="Retry the apply command"):
            apply_action._build_apply_action_plans(
                batch_name="batch",
                files={
                    "file.txt": {
                        "change_type": "deleted",
                        "mode": "100644",
                    },
                },
                selected_ids=None,
                selection_ids_to_apply=None,
                rendered=None,
                repository_root=tmp_path,
                workspace=workspace,
            )


def test_transport_size_does_not_follow_selected_id_artifacts(tmp_path):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        small = _job(workspace, selected_ids=[1])
        small_size = len(pickle.dumps(small))
        large_ids = list(range(100_000))
        large = _job(
            workspace,
            selected_ids=large_ids,
        )
        large_size = len(pickle.dumps(large))

        assert_file_job_transport_value(small)
        assert_file_job_transport_value(large)
        assert abs(large_size - small_size) < 128


def test_result_transport_size_does_not_follow_merged_content(
    tmp_path,
    monkeypatch,
):
    sizes = []
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        for byte_count in (8, 2 * 1024 * 1024):
            job = _job(workspace)

            def build_plan(**_kwargs):
                return ApplyTextPlanBuildResult(
                    plan=ApplyTextFileActionPlan(
                        "file.txt",
                        LineBuffer.from_bytes(b"x" * byte_count),
                        "100644",
                        "modified",
                    )
                )

            monkeypatch.setattr(
                text_plan_jobs._text_plan_builders,
                "build_apply_text_file_action_plan",
                build_plan,
            )
            result = compute_apply_text_plan_job(job)
            assert_file_job_transport_value(result)
            sizes.append(len(pickle.dumps(result)))

    assert abs(sizes[1] - sizes[0]) < 128
