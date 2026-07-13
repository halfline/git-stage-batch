"""Tests for staging Git index updates."""

import subprocess

import pytest

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.staging import index_update
from git_stage_batch.staging.index_update import update_index_with_blob_buffer
from git_stage_batch.utils.journal import JOURNAL_LEVEL_ENV
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

    def test_missing_index_entry_uses_executable_worktree_mode(self, temp_git_repo):
        """Generated content retains an executable worktree path's mode."""
        path = temp_git_repo / "tool"
        path.write_text("old\n")
        path.chmod(0o755)

        with LineBuffer.from_bytes(b"new\n") as buffer:
            update_index_with_blob_buffer("tool", buffer)

        result = subprocess.run(
            ["git", "ls-files", "--stage", "--", "tool"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.split()[0] == "100755"

    def test_missing_index_entry_uses_symlink_worktree_mode(self, temp_git_repo):
        """Generated symlink target bytes are installed as a symlink entry."""
        (temp_git_repo / "link").symlink_to("target")

        with LineBuffer.from_bytes(b"target") as buffer:
            update_index_with_blob_buffer("link", buffer)

        result = subprocess.run(
            ["git", "ls-files", "--stage", "--", "link"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.split()[0] == "120000"

    def test_rejects_non_blob_explicit_mode(self, temp_git_repo):
        """Gitlinks cannot be written through the blob content helper."""
        with LineBuffer.from_bytes(b"object id") as buffer:
            with pytest.raises(ValueError, match="160000"):
                update_index_with_blob_buffer("submodule", buffer, mode="160000")

    def test_disabled_journal_does_not_add_index_observations_or_previews(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Normal index updates should perform only the mode lookup they need."""
        monkeypatch.delenv(JOURNAL_LEVEL_ENV, raising=False)
        monkeypatch.delenv("GIT_STAGE_BATCH_DEBUG", raising=False)
        original_read = index_update.read_index_entry
        observed_paths = []

        def recording_read(file_path):
            observed_paths.append(file_path)
            return original_read(file_path)

        monkeypatch.setattr(index_update, "read_index_entry", recording_read)
        monkeypatch.setattr(
            index_update,
            "buffer_preview",
            lambda _buffer: pytest.fail("constructed a journal preview"),
        )

        _update_index_with_bytes("newfile.txt", b"content\n")

        assert observed_paths == ["newfile.txt"]
