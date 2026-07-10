"""Tests for file-scoped text hunk rendering."""

import subprocess

import pytest

from git_stage_batch.data.file_hunk_display import build_file_hunk_from_buffer
from git_stage_batch.editor import EditorBuffer
from git_stage_batch.utils.paths import ensure_state_directory_exists


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    ensure_state_directory_exists()

    return repo


def test_build_file_hunk_from_buffer_accepts_buffer(temp_git_repo):
    """Hypothetical file views can read generated buffers."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("line1\nline2\n")
    subprocess.run(
        ["git", "add", "test.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add test file"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )

    with EditorBuffer.from_chunks([b"line1\nchanged\n"]) as buffer:
        line_changes = build_file_hunk_from_buffer("test.txt", buffer)

    assert line_changes is not None
    assert [line.display_text() for line in line_changes.lines if line.kind == "-"] == [
        "line2"
    ]
    assert [line.display_text() for line in line_changes.lines if line.kind == "+"] == [
        "changed"
    ]
