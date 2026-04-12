"""Test case to reproduce bug where discard --file saves the wrong file to batch.

Bug: When running `discard --file --to BATCH`, the command reports saving a
different file than the one currently being displayed.

Expected: discard --file should save the currently displayed file to the batch
Actual: discard --file saves a different file (possibly alphabetically first?)
"""

import subprocess

import pytest

from .conftest import git_stage_batch


@pytest.fixture
def repo_with_multiple_files(tmp_path, monkeypatch):
    """Create a repo with multiple new files in alphabetical order."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    # Initialize git
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    # Create multiple new files (alphabetically ordered)
    (repo / "file_a.py").write_text("# File A\ndef func_a():\n    return 'A'\n")
    (repo / "file_b.py").write_text("# File B\ndef func_b():\n    return 'B'\n")
    (repo / "file_c.py").write_text("# File C\ndef func_c():\n    return 'C'\n")

    # Add with intent-to-add so files show in git diff (matches real scenario)
    subprocess.run(["git", "add", "-N", "file_a.py", "file_b.py", "file_c.py"], check=True, capture_output=True)

    return repo


def test_discard_file_discards_currently_displayed_file(repo_with_multiple_files):
    """Test that discard --file discards the file that's currently being shown.

    This reproduces a bug where discard --file was saving the wrong file to
    the batch - it would save a different file than the one currently displayed.
    """
    # Start session
    start_result = git_stage_batch("start")
    assert start_result.returncode == 0

    # Identify which file is currently displayed
    first_file = None
    if "file_a.py" in start_result.stdout:
        first_file = "file_a.py"
    elif "file_b.py" in start_result.stdout:
        first_file = "file_b.py"
    elif "file_c.py" in start_result.stdout:
        first_file = "file_c.py"

    assert first_file is not None, f"No test file found in output: {start_result.stdout[:200]}"

    # Create batch
    git_stage_batch("new", "test-batch")

    # Discard the currently displayed file
    discard_result = git_stage_batch("discard", "--file", "--to", "test-batch")
    assert discard_result.returncode == 0

    # CRITICAL: The discard output should mention the file that was displayed
    assert first_file in discard_result.stderr or first_file in discard_result.stdout, (
        f"BUG REPRODUCED: discard --file claimed to save a different file!\n"
        f"Currently displayed: {first_file}\n"
        f"Discard output: {discard_result.stdout}\n{discard_result.stderr}"
    )

    # Verify the batch contains the correct file
    show_result = git_stage_batch("show", "--from", "test-batch")
    assert show_result.returncode == 0
    assert first_file in show_result.stdout, (
        f"BUG: Batch contains wrong file!\n"
        f"Expected: {first_file}\n"
        f"Batch contents: {show_result.stdout[:500]}"
    )

    # Verify the file was removed from working tree
    import os
    file_path = repo_with_multiple_files / first_file
    assert not file_path.exists(), (
        f"BUG: File still exists in working tree after discard --file!\n"
        f"File: {first_file}"
    )


def test_discard_file_processes_files_in_display_order(repo_with_multiple_files):
    """Test that sequential discard --file operations process files in display order.

    When you run discard --file multiple times, it should process files in the
    order they're displayed, not in some other order (like alphabetical).
    """
    # Start session
    git_stage_batch("start")

    # Create batches
    git_stage_batch("new", "batch-1")
    git_stage_batch("new", "batch-2")
    git_stage_batch("new", "batch-3")

    # Track which files are processed
    processed_files = []

    # First discard
    show1 = git_stage_batch("show")
    file1 = None
    for fname in ["file_a.py", "file_b.py", "file_c.py"]:
        if fname in show1.stdout:
            file1 = fname
            break

    assert file1 is not None
    discard1 = git_stage_batch("discard", "--file", "--to", "batch-1")

    # Extract which file was actually saved (from the discard output)
    saved_file_1 = None
    for fname in ["file_a.py", "file_b.py", "file_c.py"]:
        if fname in (discard1.stdout + discard1.stderr):
            saved_file_1 = fname
            break

    assert saved_file_1 == file1, (
        f"First discard saved wrong file! Expected {file1}, got {saved_file_1}"
    )
    processed_files.append(file1)

    # Second discard
    show2 = git_stage_batch("show", check=False)
    if show2.returncode != 0:
        pytest.skip(f"No more hunks after first discard. Show output: {show2.stderr}")

    file2 = None
    for fname in ["file_a.py", "file_b.py", "file_c.py"]:
        if fname in show2.stdout and fname != file1:
            file2 = fname
            break

    if file2 is None:
        pytest.fail(
            f"Could not find second file in show output.\n"
            f"First file was: {file1}\n"
            f"Show output:\n{show2.stdout[:500]}\n"
            f"Show stderr:\n{show2.stderr[:500]}"
        )
    discard2 = git_stage_batch("discard", "--file", "--to", "batch-2")

    saved_file_2 = None
    for fname in ["file_a.py", "file_b.py", "file_c.py"]:
        if fname in (discard2.stdout + discard2.stderr):
            saved_file_2 = fname
            break

    assert saved_file_2 == file2, (
        f"Second discard saved wrong file! Expected {file2}, got {saved_file_2}"
    )
    processed_files.append(file2)

    # Third discard
    show3 = git_stage_batch("show", check=False)
    if show3.returncode == 0:
        file3 = None
        for fname in ["file_a.py", "file_b.py", "file_c.py"]:
            if fname in show3.stdout and fname not in processed_files:
                file3 = fname
                break

        if file3:
            discard3 = git_stage_batch("discard", "--file", "--to", "batch-3")

            saved_file_3 = None
            for fname in ["file_a.py", "file_b.py", "file_c.py"]:
                if fname in (discard3.stdout + discard3.stderr):
                    saved_file_3 = fname
                    break

            assert saved_file_3 == file3, (
                f"Third discard saved wrong file! Expected {file3}, got {saved_file_3}"
            )
            processed_files.append(file3)

    # Verify all three files were processed in display order (not alphabetical necessarily)
    assert len(set(processed_files)) == len(processed_files), (
        f"Duplicate files processed: {processed_files}"
    )
