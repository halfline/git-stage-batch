"""Tests for artifact-backed remaining live-change counting."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import pickle
from pathlib import Path
import subprocess
import sys
import time

import pytest

import git_stage_batch.batch.attribution as attribution_module
import git_stage_batch.batch.source.annotation as annotation_module
import git_stage_batch.data.live_change_jobs as live_jobs
import git_stage_batch.data.remaining_hunks as remaining_hunks_module
import git_stage_batch.data.selected_change.hunk_filtering as hunk_filtering_module
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.hashing import compute_stable_hunk_hash_from_lines
from git_stage_batch.core.models import SingleHunkPatch
from git_stage_batch.data.live_change_candidates import (
    stream_eligible_live_changes,
)
from git_stage_batch.data.live_change_jobs import (
    AttributionMetricsSnapshot,
    LiveTextFileCountResult,
    acquire_live_change_count_plan,
    count_eligible_live_text_file,
)
from git_stage_batch.data.remaining_hunks import estimate_remaining_hunks
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import (
    append_file_path_to_file,
    append_lines_to_file,
)
from git_stage_batch.utils.file_jobs import (
    assert_file_job_transport_value,
)
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_session_consumed_selections_file_path,
)
from tests.diff_parser_helpers import collect_unified_diff


_RUNNING_UNDER_XDIST = "PYTEST_XDIST_WORKER" in os.environ
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
_PROCESS_TEST = pytest.mark.skipif(
    sys.platform != "linux" or _RUNNING_UNDER_XDIST,
    reason="forced forkserver coverage runs on Linux with pytest -n 0",
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a repository with initialized private state paths."""
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    _git(repository, "init")
    _git(repository, "config", "user.name", "Test User")
    _git(repository, "config", "user.email", "test@example.com")
    ensure_state_directory_exists()
    return repository


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def _git_output(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_all(repository: Path, message: str = "base") -> None:
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", message)


def _lazy_candidate_count() -> int:
    count = 0
    for candidate in stream_eligible_live_changes():
        with candidate:
            count += 1
    return count


def _write_many_hunk_fixture(repository: Path, file_name: str = "many.txt") -> None:
    file_path = repository / file_name
    original = [f"line {number}\n" for number in range(1, 101)]
    file_path.write_text("".join(original))
    _commit_all(repository)
    changed = original[:]
    changed[1] = "first changed\n"
    changed[80] = "second changed\n"
    file_path.write_text("".join(changed))


def _count_with_reverse_completion_marker(
    job,
) -> LiveTextFileCountResult:
    result = count_eligible_live_text_file(job)
    time.sleep(max(0, 3 - job.ordinal) * 0.15)
    input_manifest = json.loads(Path(job.input_manifest_path).read_text())
    Path(input_manifest["scratch_directory"], "completion.marker").write_text(
        str(time.monotonic_ns())
    )
    return result


def test_text_byte_edges_match_lazy_count_and_group_by_file(temp_git_repo):
    many_path = temp_git_repo / "many.txt"
    many_lines = [f"line {number}\n" for number in range(1, 101)]
    many_path.write_text("".join(many_lines))
    (temp_git_repo / "crlf.txt").write_bytes(b"alpha\r\nbeta\r\n")
    (temp_git_repo / "no-final-newline.txt").write_bytes(b"old")
    (temp_git_repo / "unicode.txt").write_bytes("café\nold\n".encode())
    _commit_all(temp_git_repo)

    many_lines[1] = "first changed\n"
    many_lines[80] = "second changed\n"
    many_path.write_text("".join(many_lines))
    (temp_git_repo / "crlf.txt").write_bytes(b"alpha\r\nchanged\r\n")
    (temp_git_repo / "no-final-newline.txt").write_bytes(b"new")
    (temp_git_repo / "unicode.txt").write_bytes("café\n新しい\n".encode())

    assert _lazy_candidate_count() == 5
    assert estimate_remaining_hunks() == 5

    with acquire_live_change_count_plan() as plan:
        assert plan.atomic_count == 0
        jobs_by_path = {job.file_path: job for job in plan.jobs}
        assert set(jobs_by_path) == {
            "crlf.txt",
            "many.txt",
            "no-final-newline.txt",
            "unicode.txt",
        }
        many_records = [
            json.loads(line)
            for line in Path(
                jobs_by_path["many.txt"].payload.hunk_manifest_path
            ).read_text().splitlines()
        ]
        assert len(many_records) == 2
        job_artifact_directory = Path(
            jobs_by_path["many.txt"].payload.input_manifest_path
        ).parent
        assert {
            Path(record["patch_artifact_path"]).parent
            for record in many_records
        } == {job_artifact_directory}


def test_no_changes_builds_an_empty_plan(temp_git_repo):
    (temp_git_repo / "file.txt").write_text("unchanged\n")
    _commit_all(temp_git_repo)

    assert _lazy_candidate_count() == 0
    assert estimate_remaining_hunks() == 0
    with acquire_live_change_count_plan() as plan:
        assert plan.atomic_count == 0
        assert plan.jobs == ()


def test_blocked_hashes_and_paths_match_lazy_count(temp_git_repo):
    for file_name in ("a.txt", "b.txt"):
        (temp_git_repo / file_name).write_text("old\n")
    _commit_all(temp_git_repo)
    for file_name in ("a.txt", "b.txt"):
        (temp_git_repo / file_name).write_text("new\n")

    diff = subprocess.run(
        ["git", "diff", "--no-color"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    ).stdout
    patches = list(collect_unified_diff(diff.splitlines(keepends=True)))
    first_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)
    append_lines_to_file(get_block_list_file_path(), [first_hash])

    assert _lazy_candidate_count() == 1
    assert estimate_remaining_hunks() == 1

    append_file_path_to_file(get_blocked_files_file_path(), "b.txt")
    assert _lazy_candidate_count() == 0
    assert estimate_remaining_hunks() == 0


def test_rename_with_text_change_uses_one_atomic_and_one_text_count(
    temp_git_repo,
):
    old_path = temp_git_repo / "old.txt"
    lines = [f"line {number}\n" for number in range(1, 31)]
    old_path.write_text("".join(lines))
    _commit_all(temp_git_repo)

    new_path = temp_git_repo / "new.txt"
    old_path.rename(new_path)
    lines[10] = "renamed and changed\n"
    new_path.write_text("".join(lines))
    _git(temp_git_repo, "add", "-N", "new.txt")

    assert _lazy_candidate_count() == 2
    assert estimate_remaining_hunks() == 2
    with acquire_live_change_count_plan() as plan:
        assert plan.atomic_count == 1
        assert [job.file_path for job in plan.jobs] == ["new.txt"]
        result = count_eligible_live_text_file(plan.jobs[0].payload)
        assert result.eligible_count == 1


def test_atomic_changes_match_lazy_count(temp_git_repo):
    mode_path = temp_git_repo / "script.sh"
    mode_path.write_text("#!/bin/sh\nexit 0\n")
    (temp_git_repo / "deleted.txt").write_text("delete me\n")
    (temp_git_repo / "asset.bin").write_bytes(b"\x00old")
    (temp_git_repo / "old.txt").write_text("rename me\n")
    _commit_all(temp_git_repo)

    mode_path.chmod(0o755)
    (temp_git_repo / "deleted.txt").unlink()
    (temp_git_repo / "asset.bin").write_bytes(b"\x00new")
    (temp_git_repo / "old.txt").rename(temp_git_repo / "new.txt")
    _git(temp_git_repo, "add", "-N", "new.txt")

    assert _lazy_candidate_count() == 4
    assert estimate_remaining_hunks() == 4
    with acquire_live_change_count_plan() as plan:
        assert plan.atomic_count == 3
        assert [job.file_path for job in plan.jobs] == ["deleted.txt"]


def test_empty_text_deletion_remains_atomic(temp_git_repo):
    empty_path = temp_git_repo / "empty.txt"
    empty_path.write_bytes(b"")
    _commit_all(temp_git_repo)
    empty_path.unlink()

    assert _lazy_candidate_count() == 1
    assert estimate_remaining_hunks() == 1
    with acquire_live_change_count_plan() as plan:
        assert plan.atomic_count == 1
        assert plan.jobs == ()


def test_gitlink_change_remains_parent_counted(
    temp_git_repo,
):
    submodule_source = temp_git_repo.parent / "submodule-source"
    submodule_source.mkdir()
    _git(submodule_source, "init")
    _git(submodule_source, "config", "user.name", "Test User")
    _git(submodule_source, "config", "user.email", "test@example.com")
    (submodule_source / "file.txt").write_text("one\n")
    _commit_all(submodule_source, "first")

    (temp_git_repo / "README").write_text("main\n")
    _commit_all(temp_git_repo)
    _git(
        temp_git_repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(submodule_source),
        "vendor/submodule",
    )
    _git(temp_git_repo, "commit", "-am", "add submodule")

    (submodule_source / "file.txt").write_text("two\n")
    _commit_all(submodule_source, "second")
    second_commit = _git_output(submodule_source, "rev-parse", "HEAD")
    checked_out_submodule = temp_git_repo / "vendor" / "submodule"
    _git(checked_out_submodule, "fetch")
    _git(checked_out_submodule, "checkout", second_commit)

    assert _lazy_candidate_count() == 1
    assert estimate_remaining_hunks() == 1
    with acquire_live_change_count_plan() as plan:
        assert plan.atomic_count == 1
        assert plan.jobs == ()


def test_many_hunks_reuse_one_file_preparation(temp_git_repo, monkeypatch):
    _write_many_hunk_fixture(temp_git_repo)

    with acquire_live_change_count_plan() as plan:
        assert len(plan.jobs) == 1
        calls = {
            "working_tree": 0,
            "annotation_mapping": 0,
            "attribution": 0,
        }
        original_working_tree = live_jobs.load_working_tree_file_as_buffer
        original_mapping = live_jobs.acquire_batch_source_mapping
        original_attribution = live_jobs.build_file_attribution_from_lines

        def load_working_tree(*args, **kwargs):
            calls["working_tree"] += 1
            return original_working_tree(*args, **kwargs)

        @contextmanager
        def acquire_mapping(*args, **kwargs):
            calls["annotation_mapping"] += 1
            with original_mapping(*args, **kwargs) as mapping:
                yield mapping

        def build_attribution(*args, **kwargs):
            calls["attribution"] += 1
            return original_attribution(*args, **kwargs)

        monkeypatch.setattr(
            live_jobs,
            "load_working_tree_file_as_buffer",
            load_working_tree,
        )
        monkeypatch.setattr(
            live_jobs,
            "acquire_batch_source_mapping",
            acquire_mapping,
        )
        monkeypatch.setattr(
            live_jobs,
            "build_file_attribution_from_lines",
            build_attribution,
        )

        result = count_eligible_live_text_file(plan.jobs[0].payload)

    assert result.eligible_count == 2
    assert result.already_batched_count == 0
    assert calls == {
        "working_tree": 1,
        "annotation_mapping": 1,
        "attribution": 1,
    }


def test_compute_does_not_read_session_metadata(
    temp_git_repo,
    monkeypatch,
):
    file_path = temp_git_repo / "file.txt"
    file_path.write_text("old\n")
    _commit_all(temp_git_repo)
    file_path.write_text("new\n")

    with acquire_live_change_count_plan() as plan:
        assert len(plan.jobs) == 1

        def unexpected_read(*_args, **_kwargs):
            raise AssertionError("unexpected session metadata read")

        monkeypatch.setattr(
            live_jobs,
            "load_session_batch_sources",
            unexpected_read,
        )
        monkeypatch.setattr(
            live_jobs,
            "load_consumed_selections_metadata",
            unexpected_read,
        )
        monkeypatch.setattr(
            attribution_module,
            "list_batch_names",
            unexpected_read,
        )
        monkeypatch.setattr(
            attribution_module,
            "read_batch_metadata_for_batches",
            unexpected_read,
        )
        monkeypatch.setattr(
            annotation_module,
            "get_batch_source_for_file",
            unexpected_read,
        )
        monkeypatch.setattr(
            hunk_filtering_module,
            "read_consumed_file_metadata",
            unexpected_read,
        )
        monkeypatch.setattr(
            hunk_filtering_module,
            "log_journal",
            unexpected_read,
        )

        result = count_eligible_live_text_file(plan.jobs[0].payload)

    assert result.eligible_count == 1
    assert result.stale is False


def test_empty_lifecycle_filter_uses_captured_metadata(
    temp_git_repo,
    monkeypatch,
):
    (temp_git_repo / "base.txt").write_text("base\n")
    _commit_all(temp_git_repo)
    empty_path = temp_git_repo / "empty.txt"
    empty_path.write_bytes(b"")
    _git(temp_git_repo, "add", "-N", "empty.txt")

    with acquire_live_change_count_plan() as plan:
        assert len(plan.jobs) == 1
        job = plan.jobs[0].payload
        input_path = Path(job.input_manifest_path)
        input_manifest = json.loads(input_path.read_text())
        input_manifest["batch_metadata_by_name"] = {
            "empty-batch": {
                "files": {
                    "empty.txt": {
                        "batch_source_commit": input_manifest["head_commit"],
                        "change_type": "added",
                    },
                },
            },
        }
        input_path.write_text(
            json.dumps(input_manifest, ensure_ascii=True, separators=(",", ":"))
            + "\n"
        )
        monkeypatch.setattr(
            hunk_filtering_module._change_freshness,
            "empty_text_lifecycle_change_is_batched",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("unexpected mutable lifecycle read")
            ),
        )

        result = count_eligible_live_text_file(job)

    assert result.eligible_count == 0
    assert result.already_batched_count == 1


def test_consumed_replacement_masks_are_loaded_once_and_applied(
    temp_git_repo,
):
    file_path = temp_git_repo / "file.txt"
    file_path.write_text("old\n")
    _commit_all(temp_git_repo)
    head_commit = _git_output(temp_git_repo, "rev-parse", "HEAD")
    file_path.write_text("new\n")

    consumed_path = get_session_consumed_selections_file_path()
    consumed_path.parent.mkdir(parents=True, exist_ok=True)
    consumed_path.write_text(
        json.dumps({
            "files": {
                "file.txt": {
                    "batch_source_commit": head_commit,
                    "presence_claims": [],
                    "deletions": [],
                    "replacement_masks": [
                        {
                            "deleted_lines": ["old"],
                            "added_lines": ["new"],
                        },
                    ],
                },
            },
        })
    )

    assert _lazy_candidate_count() == 0
    assert estimate_remaining_hunks() == 0
    with acquire_live_change_count_plan() as plan:
        assert len(plan.jobs) == 1
        result = count_eligible_live_text_file(plan.jobs[0].payload)
        assert result.eligible_count == 0
        assert result.already_batched_count == 1


def test_parser_buffers_can_close_immediately_after_spooling(
    temp_git_repo,
    monkeypatch,
):
    for file_name in ("a.txt", "b.txt"):
        (temp_git_repo / file_name).write_text("old\n")
    _commit_all(temp_git_repo)

    path_sequence = ("a.txt", "b.txt", "a.txt")
    parser_buffers = []

    @contextmanager
    def acquire_fake_diff(_lines):
        def changes():
            for path in path_sequence:
                buffer = LineBuffer.from_chunks(
                    (
                        f"--- a/{path}\n".encode(),
                        f"+++ b/{path}\n".encode(),
                        b"@@ -1 +1 @@\n",
                        b"-old\n",
                        b"+new\n",
                    )
                )
                parser_buffers.append(buffer)
                try:
                    yield SingleHunkPatch(path, path, buffer)
                finally:
                    buffer.close()

        try:
            yield changes()
        finally:
            for buffer in parser_buffers:
                buffer.close()

    monkeypatch.setattr(live_jobs, "acquire_unified_diff", acquire_fake_diff)
    monkeypatch.setattr(live_jobs, "stream_live_git_diff", lambda **_kwargs: ())

    with acquire_live_change_count_plan() as plan:
        assert [job.file_path for job in plan.jobs] == list(path_sequence)
        patch_paths = []
        for job in plan.jobs:
            records = [
                json.loads(line)
                for line in Path(
                    job.payload.hunk_manifest_path
                ).read_text().splitlines()
            ]
            assert len(records) == 1
            patch_paths.append(Path(records[0]["patch_artifact_path"]))
        assert all(path.read_bytes().endswith(b"+new\n") for path in patch_paths)

    for buffer in parser_buffers:
        with pytest.raises(ValueError, match="closed"):
            next(buffer.byte_chunks())


def test_job_and_result_transport_stay_content_independent(temp_git_repo):
    file_path = temp_git_repo / "file.txt"
    file_path.write_text("old\n")
    _commit_all(temp_git_repo)
    file_path.write_text("new\n")

    with acquire_live_change_count_plan() as plan:
        job = plan.jobs[0].payload
        serialized_job = pickle.dumps(job)
        hunk_records = [
            json.loads(line)
            for line in Path(job.hunk_manifest_path).read_text().splitlines()
        ]
        patch_path = Path(hunk_records[0]["patch_artifact_path"])
        patch_path.write_bytes(b"x" * (2 * 1024 * 1024))

        assert pickle.dumps(job) == serialized_job
        assert len(serialized_job) < 1024
        assert b"x" * 100 not in serialized_job
        assert_file_job_transport_value(job)

    result = LiveTextFileCountResult(
        ordinal=0,
        file_path="file.txt",
        eligible_count=1,
        already_batched_count=0,
        attribution_metrics=AttributionMetricsSnapshot(),
    )
    assert len(pickle.dumps(result)) < 1024
    assert_file_job_transport_value(result)


def test_builder_loads_shared_snapshots_once(
    temp_git_repo,
    monkeypatch,
):
    for file_name in ("a.txt", "b.txt"):
        (temp_git_repo / file_name).write_text("old\n")
    _commit_all(temp_git_repo)
    for file_name in ("a.txt", "b.txt"):
        (temp_git_repo / file_name).write_text("new\n")

    calls = {
        "batch_names": 0,
        "batch_metadata": 0,
        "batch_sources": 0,
        "consumed": 0,
        "head": 0,
        "state_commits": 0,
    }
    original_list_batch_names = live_jobs.list_batch_names
    original_batch_metadata = live_jobs.read_batch_metadata_for_batches
    original_batch_sources = live_jobs.load_session_batch_sources
    original_consumed = live_jobs.load_consumed_selections_metadata
    original_head = live_jobs.current_head_commit
    original_resolve = live_jobs.resolve_git_objects

    def list_batch_names():
        calls["batch_names"] += 1
        return original_list_batch_names()

    def read_batch_metadata(batch_names, **kwargs):
        calls["batch_metadata"] += 1
        return original_batch_metadata(batch_names, **kwargs)

    def load_batch_sources():
        calls["batch_sources"] += 1
        return original_batch_sources()

    def load_consumed():
        calls["consumed"] += 1
        return original_consumed()

    def read_head():
        calls["head"] += 1
        return original_head()

    def resolve_state_commits(object_names):
        calls["state_commits"] += 1
        return original_resolve(object_names)

    monkeypatch.setattr(live_jobs, "list_batch_names", list_batch_names)
    monkeypatch.setattr(
        live_jobs,
        "read_batch_metadata_for_batches",
        read_batch_metadata,
    )
    monkeypatch.setattr(
        live_jobs,
        "load_session_batch_sources",
        load_batch_sources,
    )
    monkeypatch.setattr(
        live_jobs,
        "load_consumed_selections_metadata",
        load_consumed,
    )
    monkeypatch.setattr(live_jobs, "current_head_commit", read_head)
    monkeypatch.setattr(
        live_jobs,
        "resolve_git_objects",
        resolve_state_commits,
    )

    with acquire_live_change_count_plan() as plan:
        assert len(plan.jobs) == 2

    assert calls == {
        "batch_names": 1,
        "batch_metadata": 1,
        "batch_sources": 1,
        "consumed": 1,
        "head": 1,
        "state_commits": 1,
    }
