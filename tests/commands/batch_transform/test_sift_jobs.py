"""Tests for artifact-backed text sift jobs."""

from __future__ import annotations

import json
from pathlib import Path
import pickle

import pytest

from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.commands.batch_transform.sift_jobs import (
    SiftTextFileJob,
    SiftTextFileJobResult,
    compute_sifted_text_file_job,
    load_sifted_text_file_result,
    validate_sifted_text_file_job_result,
)
from git_stage_batch.commands.batch_transform.sift_results import (
    SiftedTextFileResult,
)
import git_stage_batch.commands.batch_transform.sift_jobs as sift_jobs
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.data.file_target_identity import WorktreeIdentity
from git_stage_batch.exceptions import MergeError
from git_stage_batch.utils.file_job_workspace import FileJobWorkspace
from git_stage_batch.utils.file_jobs import assert_file_job_transport_value


def _job(workspace: FileJobWorkspace, *, ordinal: int = 0) -> SiftTextFileJob:
    worktree_path = workspace.write_buffer(
        ordinal,
        "worktree-input",
        (b"working\n",),
    )
    input_path = workspace.write_pickle(
        ordinal,
        "sift-input.pickle",
        {
            "baseline_object_id": "a" * 40,
            "batch_source_object_id": "b" * 40,
            "file_meta": {"change_type": "modified"},
            "working_tree_artifact_path": str(worktree_path),
        },
    )
    target_path = workspace.output_path(ordinal, "target.bin")
    manifest_path = workspace.output_path(ordinal, "manifest.json")
    return SiftTextFileJob(
        ordinal=ordinal,
        file_path="file.txt",
        input_artifact_path=str(input_path),
        target_output_path=str(target_path),
        manifest_output_path=str(manifest_path),
        deletion_output_directory=str(target_path.parent / "deletions"),
        scratch_directory=str(workspace.scratch_directory(ordinal)),
        expected_worktree_identity=WorktreeIdentity(
            True,
            "regular",
            0o644,
            8,
            "digest",
        ),
    )


def _retained_result() -> SiftedTextFileResult:
    deletion = LineBuffer.from_bytes(b"old\n")
    return SiftedTextFileResult(
        ownership=BatchOwnership.from_presence_lines(
            ["2-3"],
            [AbsenceClaim(anchor_line=1, content_lines=deletion)],
        ),
        target_buffer=LineBuffer.from_bytes(b"base\nnew\nmore\n"),
        change_type="modified",
    )


def test_compute_streams_target_and_deletions_to_manifest(
    tmp_path,
    monkeypatch,
):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        source_result = _retained_result()
        captured = {}

        def compute(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return source_result

        monkeypatch.setattr(
            sift_jobs._sift_results,
            "compute_sifted_text_file",
            compute,
        )

        result = compute_sifted_text_file_job(job)

        validate_sifted_text_file_job_result(job, result)
        assert_file_job_transport_value(result)
        assert Path(job.target_output_path).read_bytes() == b"base\nnew\nmore\n"
        manifest = json.loads(Path(job.manifest_output_path).read_text())
        assert manifest["presence_lines"] == ["2-3"]
        assert manifest["output_order"] == 0
        assert manifest["deletions"] == [
            {
                "anchor_line": 1,
                "content_path": str(
                    Path(job.deletion_output_directory) / "00000000-content.bin"
                ),
                "output_order": 0,
            }
        ]
        assert Path(manifest["deletions"][0]["content_path"]).read_bytes() == b"old\n"
        assert captured["args"] == ("file.txt", {"change_type": "modified"})
        assert captured["kwargs"]["spool_dir"] == job.scratch_directory
        with pytest.raises(ValueError, match="buffer is closed"):
            source_result.target_buffer.to_bytes()

        loaded = load_sifted_text_file_result(workspace, job, result)
        try:
            assert loaded.target_buffer.to_bytes() == b"base\nnew\nmore\n"
            assert loaded.ownership.presence_line_set().to_range_strings() == ["2-3"]
            assert loaded.ownership.deletions[0].content_lines.to_bytes() == b"old\n"
        finally:
            loaded.close()


def test_compute_reports_removed_result_without_artifacts(tmp_path, monkeypatch):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        monkeypatch.setattr(
            sift_jobs._sift_results,
            "compute_sifted_text_file",
            lambda *args, **kwargs: None,
        )

        result = compute_sifted_text_file_job(job)

        assert result == SiftTextFileJobResult(0, "file.txt", "removed")
        assert_file_job_transport_value(result)
        assert not Path(job.target_output_path).exists()
        assert not Path(job.manifest_output_path).exists()


def test_compute_reports_merge_error_as_ordered_scalar(tmp_path, monkeypatch):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)

        def fail(*args, **kwargs):
            raise MergeError("ambiguous placement")

        monkeypatch.setattr(
            sift_jobs._sift_results,
            "compute_sifted_text_file",
            fail,
        )

        result = compute_sifted_text_file_job(job)

        assert result == SiftTextFileJobResult(
            0,
            "file.txt",
            "merge_error",
            error_message="ambiguous placement",
        )
        validate_sifted_text_file_job_result(job, result)
        assert_file_job_transport_value(result)


def test_compute_refuses_unrepresented_ownership_fields(tmp_path, monkeypatch):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        source_result = _retained_result()
        source_result.ownership.replacement_units.append(object())
        monkeypatch.setattr(
            sift_jobs._sift_results,
            "compute_sifted_text_file",
            lambda *args, **kwargs: source_result,
        )

        with pytest.raises(
            ValueError,
            match="do not support replacement units",
        ):
            compute_sifted_text_file_job(job)

        with pytest.raises(ValueError, match="buffer is closed"):
            source_result.target_buffer.to_bytes()


def test_loader_rejects_manifest_path_substitution(tmp_path, monkeypatch):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        monkeypatch.setattr(
            sift_jobs._sift_results,
            "compute_sifted_text_file",
            lambda *args, **kwargs: _retained_result(),
        )
        result = compute_sifted_text_file_job(job)
        manifest = json.loads(Path(job.manifest_output_path).read_text())
        manifest["deletions"][0]["content_path"] = str(tmp_path / "outside")
        Path(job.manifest_output_path).write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match="mismatched deletion path"):
            load_sifted_text_file_result(workspace, job, result)


def test_loader_rejects_empty_deletion_content(tmp_path, monkeypatch):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        monkeypatch.setattr(
            sift_jobs._sift_results,
            "compute_sifted_text_file",
            lambda *args, **kwargs: _retained_result(),
        )
        result = compute_sifted_text_file_job(job)
        deletion_path = (
            Path(job.deletion_output_directory) / "00000000-content.bin"
        )
        deletion_path.write_bytes(b"")

        with pytest.raises(ValueError, match="must not be empty"):
            load_sifted_text_file_result(workspace, job, result)


def test_job_and_result_pickle_sizes_exclude_file_content(tmp_path):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)
        Path(job.target_output_path).write_bytes(b"x" * (2 * 1024 * 1024))
        result = SiftTextFileJobResult(
            ordinal=job.ordinal,
            file_path=job.file_path,
            outcome="retained",
            manifest_path=job.manifest_output_path,
            target_path=job.target_output_path,
        )

        assert len(pickle.dumps(job)) < 2_000
        assert len(pickle.dumps(result)) < 1_000
        assert_file_job_transport_value(job)
        assert_file_job_transport_value(result)


@pytest.mark.parametrize(
    "result, message",
    [
        (
            SiftTextFileJobResult(1, "file.txt", "removed"),
            "mismatched result",
        ),
        (
            SiftTextFileJobResult(0, "file.txt", "retained"),
            "omitted its manifest",
        ),
        (
            SiftTextFileJobResult(
                0,
                "file.txt",
                "merge_error",
                error_message=None,
            ),
            "omitted its error",
        ),
    ],
)
def test_result_validation_rejects_malformed_responses(
    tmp_path,
    result,
    message,
):
    with FileJobWorkspace(parent_directory=tmp_path) as workspace:
        job = _job(workspace)

        with pytest.raises(ValueError, match=message):
            validate_sifted_text_file_job_result(job, result)
