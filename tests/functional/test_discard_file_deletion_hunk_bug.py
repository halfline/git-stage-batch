"""Test case for bug where discard --file causes empty deletion hunks to appear.

Bug: After running `discard --file --to BATCH` on a new file, the session
gets stuck showing an empty deletion hunk for that same file instead of
advancing to the next file.

Root cause: When a new file is removed from the working tree by discard --file,
git sees it as a deletion. The session then processes this deletion as a "hunk",
creating an empty patch that blocks progress.
"""

import subprocess

import pytest

from .conftest import git_stage_batch


@pytest.fixture
def repo_with_new_files(tmp_path, monkeypatch):
    """Create a repo with multiple new files."""
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

    # Create new files and add with intent-to-add
    (repo / "file_a.py").write_text("# File A\ndef func_a():\n    return 'A'\n")
    (repo / "file_b.py").write_text("# File B\ndef func_b():\n    return 'B'\n")
    (repo / "file_c.py").write_text("# File C\ndef func_c():\n    return 'C'\n")

    subprocess.run(["git", "add", "-N", "file_a.py", "file_b.py", "file_c.py"],
                   check=True, capture_output=True)

    return repo


def test_discard_file_advances_to_next_file_not_deletion_hunk(repo_with_new_files):
    """Test that discard --file advances to the next file, not an empty deletion hunk.

    When you discard a new file with `discard --file`, the session should
    advance to the next file's hunk, not get stuck showing an empty deletion
    hunk for the file that was just removed.
    """
    # Start session
    start_result = git_stage_batch("start")
    assert start_result.returncode == 0

    # Identify first file
    first_file = None
    for fname in ["file_a.py", "file_b.py", "file_c.py"]:
        if fname in start_result.stdout:
            first_file = fname
            break

    assert first_file is not None, f"No file found in start output: {start_result.stdout[:200]}"

    # Create batch and discard first file
    git_stage_batch("new", "test-batch")
    discard_result = git_stage_batch("discard", "--file", "--to", "test-batch")
    assert discard_result.returncode == 0

    # Show should now display a DIFFERENT file, not the one we just discarded
    show_result = git_stage_batch("show", check=False)

    if show_result.returncode != 0:
        # No more hunks is acceptable
        return

    # BUG CHECK: The show output should NOT be the file we just discarded
    assert first_file not in show_result.stdout, (
        f"BUG REPRODUCED: After discarding {first_file}, show is still displaying it!\n"
        f"This suggests the session is stuck on an empty deletion hunk.\n"
        f"Show output:\n{show_result.stdout[:500]}"
    )

    # The show output should be one of the OTHER files
    other_files = [f for f in ["file_a.py", "file_b.py", "file_c.py"] if f != first_file]
    found_other_file = any(fname in show_result.stdout for fname in other_files)

    assert found_other_file, (
        f"BUG: After discarding {first_file}, show is not displaying any other file!\n"
        f"Expected one of: {other_files}\n"
        f"Show output:\n{show_result.stdout[:500]}"
    )


def test_discard_file_does_not_create_empty_hunks(repo_with_new_files):
    """Test that discard --file doesn't create empty @@ -0,0 +0,0 @@ hunks.

    After discarding a file, if we see a hunk header like @@ -0,0 +0,0 @@,
    that's a sign of the bug - an empty deletion hunk is being processed.
    """
    # Start session
    git_stage_batch("start")
    git_stage_batch("new", "test-batch")

    # Discard first file
    discard_result = git_stage_batch("discard", "--file", "--to", "test-batch")
    assert discard_result.returncode == 0

    # Show next hunk
    show_result = git_stage_batch("show", check=False)

    if show_result.returncode != 0:
        # No more hunks is fine
        return

    # BUG CHECK: Empty hunk headers indicate the deletion bug
    assert "@@ -0,0 +0,0 @@" not in show_result.stdout, (
        f"BUG REPRODUCED: Empty deletion hunk found after discard --file!\n"
        f"This indicates git-stage-batch is processing the deletion as a hunk.\n"
        f"Show output:\n{show_result.stdout[:500]}"
    )

    # Also check for hunks with no actual content (just the file name and header)
    lines = show_result.stdout.strip().split('\n')
    # A real hunk should have at least: file line, hunk header, and some content lines
    if len(lines) >= 2 and lines[0].endswith('::') and lines[1].startswith('@@'):
        # This looks like a hunk. Check if there's content after the header
        content_lines = [l for l in lines[2:] if l.strip()]
        assert len(content_lines) > 0, (
            f"BUG: Hunk has header but no content lines!\n"
            f"Show output:\n{show_result.stdout[:500]}"
        )
