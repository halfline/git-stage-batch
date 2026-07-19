"""Functional tests for status command."""


from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from .conftest import git_stage_batch


_RUNNING_UNDER_XDIST = "PYTEST_XDIST_WORKER" in os.environ
_PROCESS_TEST = pytest.mark.skipif(
    sys.platform != "linux" or _RUNNING_UNDER_XDIST,
    reason="forced forkserver coverage runs on Linux with pytest -n 0",
)


def _git_output(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _session_snapshot(repository: Path) -> dict[str, bytes]:
    state_directory = repository / ".git" / "git-stage-batch"
    if not state_directory.exists():
        return {}
    return {
        str(path.relative_to(state_directory)): path.read_bytes()
        for path in sorted(state_directory.rglob("*"))
        if path.is_file() and path.name != "session.lock"
    }


def _repository_snapshot(repository: Path) -> tuple[str, str, dict[str, bytes]]:
    return (
        _git_output(
            repository,
            "status",
            "--porcelain=v2",
            "--untracked-files=all",
        ),
        _git_output(repository, "show-ref", "--head"),
        _session_snapshot(repository),
    )


def _normalized_journal(path: Path) -> list[dict]:
    entries = [
        json.loads(line)
        for line in path.read_text().splitlines()
    ]
    assert len({entry["pid"] for entry in entries}) == 1
    return [
        {
            key: value
            for key, value in entry.items()
            if key not in {"pid", "timestamp"}
        }
        for entry in entries
    ]


class TestStatusCommand:
    """Test status command."""

    def test_status_with_no_session(self, repo_with_changes):
        """Test status when no session is active."""
        result = git_stage_batch("status")
        # Should indicate no session or show session status
        assert result.returncode == 0

    def test_status_after_start(self, repo_with_changes):
        """Test status after starting a session."""
        git_stage_batch("start")

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show session information

    def test_status_shows_staged_changes(self, repo_with_changes):
        """Test status shows staged changes."""
        git_stage_batch("start")
        git_stage_batch("include", "--line", "1", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show some indication of staged changes

    def test_status_after_batch_operations(self, repo_with_changes):
        """Test status after batch operations."""
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "1", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show session status

    def test_status_shows_progress(self, repo_with_changes):
        """Test status shows progress through hunks."""
        git_stage_batch("start")

        # Skip a few hunks
        for _ in range(3):
            git_stage_batch("skip", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show progress information

    def test_status_st_shorthand(self, repo_with_changes):
        """Test 'st' shorthand for status."""
        git_stage_batch("start")

        result = git_stage_batch("st")
        assert result.returncode == 0
        # Should work same as 'status'

    def test_status_with_multiple_files_staged(self, repo_with_changes):
        """Test status with multiple files staged."""
        git_stage_batch("start")

        # Stage from multiple hunks
        for _ in range(5):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break
            git_stage_batch("include", "--line", "1", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show multiple files

    def test_status_outside_repo(self, tmp_path, monkeypatch):
        """Test status outside a git repo."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        result = git_stage_batch("status", check=False)
        # Should fail or indicate not in a repo
        assert result.returncode != 0 or "not a git" in result.stderr.lower()

    def test_status_after_abort(self, repo_with_changes):
        """Test status after aborting a session."""
        git_stage_batch("start")
        git_stage_batch("include", "--line", "1", check=False)
        git_stage_batch("abort")

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should indicate no active session

    @_PROCESS_TEST
    def test_forced_process_matches_inline_across_worker_counts_and_hash_seeds(
        self,
        repo_with_changes,
        monkeypatch,
        tmp_path,
    ):
        """Status output, journals, and repository state must be transport-neutral."""
        extra_path = repo_with_changes / "extra.txt"
        extra_path.write_text("old\n")
        subprocess.run(
            ["git", "add", "extra.txt"],
            cwd=repo_with_changes,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add process status fixture"],
            cwd=repo_with_changes,
            check=True,
            capture_output=True,
        )
        extra_path.write_text("new\n")
        git_stage_batch("start")

        expected_state = _repository_snapshot(repo_with_changes)
        observed = []
        for requested_jobs, hash_seed in (
            ("1", "11"),
            ("2", "22"),
            ("4", "33"),
        ):
            journal_path = tmp_path / f"journal-{requested_jobs}.jsonl"
            monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", requested_jobs)
            monkeypatch.setenv("PYTHONHASHSEED", hash_seed)
            monkeypatch.setenv("GIT_STAGE_BATCH_JOURNAL", "metadata-only")
            monkeypatch.setenv(
                "GIT_STAGE_BATCH_JOURNAL_PATH",
                str(journal_path),
            )

            result = git_stage_batch("status", "--porcelain")

            assert _repository_snapshot(repo_with_changes) == expected_state
            observed.append((
                result.stdout,
                result.stderr,
                _normalized_journal(journal_path),
            ))

        assert observed[1:] == observed[:1] * 2
        assert json.loads(observed[0][0])["progress"]["remaining"] == 4


class TestStatusWithBatches:
    """Test status command with batches."""

    def test_status_shows_batches(self, repo_with_changes):
        """Test status shows available batches."""
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")

        result = git_stage_batch("status")
        assert result.returncode == 0
        # May show batches or at least not fail

    def test_status_after_batch_save(self, repo_with_changes):
        """Test status after saving to batch."""
        git_stage_batch("new", "save-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "save-batch", "1,2", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0

    def test_status_with_empty_batch(self, repo_with_changes):
        """Test status with empty batch."""
        git_stage_batch("new", "empty-batch")

        result = git_stage_batch("status")
        assert result.returncode == 0
