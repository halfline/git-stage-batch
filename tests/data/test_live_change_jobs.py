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
