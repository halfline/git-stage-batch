"""Tests for git index and tree plumbing helpers."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_index import (
    git_commit_tree,
    git_read_tree,
    git_update_index,
    git_update_index_entries,
    GitIndexEntryUpdate,
    git_write_tree,
    temp_git_index,
)
from git_stage_batch.utils.git_object_io import create_git_blob


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    return repo


class TestGitIndexPlumbing:
    """Tests for temporary index plumbing helpers."""

    def test_temp_index_builds_commit_without_touching_main_index(self, temp_git_repo):
        """Test creating a commit from a temporary index."""
        blob_sha = create_git_blob([b"from temp index\n"])

        with temp_git_index() as env:
            temp_index_path = Path(env["GIT_INDEX_FILE"])
            git_read_tree("HEAD", env=env)
            git_update_index(
                mode="100644",
                blob_sha=blob_sha,
                file_path="nested/file.txt",
                env=env,
            )
            tree_sha = git_write_tree(env=env)

        assert not temp_index_path.exists()

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Temporary index commit",
        )
        result = run_git_command(["show", f"{commit_sha}:nested/file.txt"])

        assert result.stdout == "from temp index\n"
        assert run_git_command(["status", "--short"]).stdout == ""

    def test_update_index_cacheinfo_handles_comma_paths(self, temp_git_repo):
        """Test that cacheinfo paths are passed as separate arguments."""
        blob_sha = create_git_blob([b"comma path\n"])
        file_path = "dir/name,with,commas.txt"

        with temp_git_index() as env:
            git_read_tree("HEAD", env=env)
            git_update_index(
                mode="100644",
                blob_sha=blob_sha,
                file_path=file_path,
                env=env,
            )
            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Comma path commit",
        )
        result = run_git_command(["show", f"{commit_sha}:{file_path}"])

        assert result.stdout == "comma path\n"

    def test_update_index_force_remove_deletes_index_entry(self, temp_git_repo):
        """Test force-removing a path from a temporary index."""
        with temp_git_index() as env:
            git_read_tree("HEAD", env=env)
            git_update_index(file_path="README.md", force_remove=True, env=env)
            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Remove file from temp index",
        )
        result = run_git_command(["show", f"{commit_sha}:README.md"], check=False)

        assert result.returncode != 0
        assert run_git_command(["status", "--short"]).stdout == ""

    def test_batched_force_remove_uses_repository_object_width(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Index-info removal should use a SHA-256-width null object ID."""
        repo = tmp_path / "sha256-index"
        subprocess.run(
            ["git", "init", "--object-format=sha256", str(repo)],
            check=True,
            capture_output=True,
        )
        monkeypatch.chdir(repo)
        (repo / "tracked.txt").write_text("tracked\n")
        subprocess.run(["git", "add", "tracked.txt"], check=True, cwd=repo)

        git_update_index_entries(
            [GitIndexEntryUpdate(file_path="tracked.txt", force_remove=True)]
        )

        assert run_git_command(["ls-files", "tracked.txt"]).stdout == ""

    def test_update_index_rejects_ambiguous_modes(self, temp_git_repo):
        """Test that update-index helper modes are explicit."""
        with pytest.raises(ValueError, match="mode and blob_sha are required"):
            git_update_index(file_path="README.md")

        with pytest.raises(ValueError, match="cannot be used with force_remove"):
            git_update_index(
                file_path="README.md",
                mode="100644",
                blob_sha="0" * 40,
                force_remove=True,
            )
