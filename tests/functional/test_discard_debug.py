"""Debug test to see what's happening with discard --file."""

import subprocess

from .conftest import git_stage_batch


def test_debug_discard_file_patches(repo_with_changes):
    """Debug: Check if patches are being found for new files."""
    repo = repo_with_changes

    # Create a small new file
    new_file = repo / "debug.py"
    new_file.write_text("line1\nline2\nline3\n")

    # Add with intent-to-add
    subprocess.run(["git", "add", "-N", "debug.py"], check=True, capture_output=True)

    # Check what git diff shows
    diff_result = subprocess.run(
        ["git", "diff"],
        capture_output=True,
        text=True,
        check=True
    )
    print(f"\n=== GIT DIFF OUTPUT ===\n{diff_result.stdout}")
    assert "debug.py" in diff_result.stdout, "File should appear in git diff"
    assert "line1" in diff_result.stdout, "Content should be in diff"

    # Start session
    git_stage_batch("start")

    # Navigate to debug.py by skipping other files
    for _ in range(20):  # Max 20 skips to find our file
        show_result = git_stage_batch("show", check=False)
        if show_result.returncode != 0:
            break
        if "debug.py" in show_result.stdout:
            print(f"\n=== FOUND DEBUG.PY ===\n{show_result.stdout[:200]}")
            break
        git_stage_batch("skip")
    else:
        assert False, "Couldn't find debug.py in hunks!"

    # Create batch and discard
    git_stage_batch("new", "debug-batch")
    discard_result = git_stage_batch("discard", "--file", "--to", "debug-batch", check=False)
    print(f"\n=== DISCARD OUTPUT ===\n{discard_result.stdout}\n{discard_result.stderr}")
    print(f"Return code: {discard_result.returncode}")

    # Check file status
    print(f"\nFile exists: {new_file.exists()}")
    if new_file.exists():
        content = new_file.read_text()
        print(f"File content ({len(content)} chars): {repr(content)}")

    assert not new_file.exists(), f"File should be deleted! Still has: {new_file.read_text() if new_file.exists() else 'N/A'}"
