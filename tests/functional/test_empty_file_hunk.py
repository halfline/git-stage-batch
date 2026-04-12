"""Test that empty new files are shown as hunks."""

import subprocess

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.utils.paths import get_selected_hunk_patch_file_path
from git_stage_batch.utils.file_io import read_text_file_contents


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    # Create initial commit
    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    return tmp_path


def test_empty_new_file_shows_as_hunk(temp_git_repo):
    """Test that a new empty file appears as a hunk in the diff."""
    # Create an empty file
    empty_file = temp_git_repo / "empty.py"
    empty_file.write_text("")

    # Start session
    command_start()

    # Find first hunk
    hunk = fetch_next_change()

    # Should find the empty file as a hunk
    assert hunk is not None, "Empty file should appear as a hunk"

    # Load the patch
    patch_text = read_text_file_contents(get_selected_hunk_patch_file_path())

    # Patch should mention the empty file
    assert "empty.py" in patch_text, f"Patch should mention empty.py, got: {patch_text}"

    # Should be a new file mode
    assert "+++ b/empty.py" in patch_text or "new file mode" in patch_text


def test_empty_file_in_directory_shows_as_hunk(temp_git_repo):
    """Test that a new empty __init__.py file appears as a hunk."""
    # Create a directory with an empty __init__.py
    new_dir = temp_git_repo / "newdir"
    new_dir.mkdir()
    init_file = new_dir / "__init__.py"
    init_file.write_text("")

    # Start session
    command_start()

    # Find first hunk
    hunk = fetch_next_change()

    # Should find the empty __init__.py as a hunk
    assert hunk is not None, "Empty __init__.py should appear as a hunk"

    # Load the patch
    patch_text = read_text_file_contents(get_selected_hunk_patch_file_path())

    # Patch should mention the file
    assert "__init__.py" in patch_text or "newdir" in patch_text, \
        f"Patch should mention __init__.py or newdir, got: {patch_text}"
