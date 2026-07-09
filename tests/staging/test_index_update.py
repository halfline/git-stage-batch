"""Tests for staging Git index updates."""

import subprocess

import pytest

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.staging.index_update import update_index_with_blob_buffer
from git_stage_batch.utils.paths import ensure_state_directory_exists


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

    return repo


def _update_index_with_bytes(path: str, data: bytes) -> None:
    with LineBuffer.from_bytes(data) as buffer:
        update_index_with_blob_buffer(path, buffer)


class TestUpdateIndexWithBlobContent:
    """Tests for update_index_with_blob_buffer."""

    def test_update_new_file(self, temp_git_repo):
        """Test updating index with a new file."""
        ensure_state_directory_exists()

        (temp_git_repo / "existing.txt").write_text("existing\n")
        subprocess.run(
            ["git", "add", "existing.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        _update_index_with_bytes("newfile.txt", b"new content\n")

        result = subprocess.run(
            ["git", "ls-files", "--", "newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "newfile.txt" in result.stdout

        result = subprocess.run(
            ["git", "show", ":newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "new content\n"

    def test_update_existing_file(self, temp_git_repo):
        """Test updating an existing file in the index."""
        ensure_state_directory_exists()

        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        _update_index_with_bytes("file.txt", b"modified\n")

        result = subprocess.run(
            ["git", "show", ":file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "modified\n"
        assert (temp_git_repo / "file.txt").read_text() == "original\n"

    def test_preserves_file_mode(self, temp_git_repo):
        """Test that file mode is preserved when updating."""
        ensure_state_directory_exists()

        (temp_git_repo / "script.sh").write_text("#!/bin/bash\necho hello\n")
        subprocess.run(
            ["git", "add", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "update-index", "--chmod=+x", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        original_mode = result.stdout.split()[0]

        _update_index_with_bytes("script.sh", b"#!/bin/bash\necho goodbye\n")

        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        new_mode = result.stdout.split()[0]
        assert new_mode == original_mode

    def test_defaults_to_regular_file_mode(self, temp_git_repo):
        """Test that new files get regular file mode (100644)."""
        ensure_state_directory_exists()

        (temp_git_repo / "dummy.txt").write_text("dummy\n")
        subprocess.run(
            ["git", "add", "dummy.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        _update_index_with_bytes("newfile.txt", b"content\n")

        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        mode = result.stdout.split()[0]
        assert mode == "100644"

    def test_update_new_file_from_buffer(self, temp_git_repo):
        """Index updates can read buffers."""
        ensure_state_directory_exists()
        (temp_git_repo / "existing.txt").write_text("existing\n")
        subprocess.run(
            ["git", "add", "existing.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        with LineBuffer.from_chunks([b"generated\n", b"content\n"]) as buffer:
            update_index_with_blob_buffer("generated.txt", buffer)

        result = subprocess.run(
            ["git", "show", ":generated.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "generated\ncontent\n"
