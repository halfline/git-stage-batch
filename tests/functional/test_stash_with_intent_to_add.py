"""Test that stash creation works even with intent-to-add files in index."""

from pathlib import Path
import json

import subprocess

from .conftest import git_stage_batch


def test_stash_created_despite_intent_to_add_files(repo_with_changes):
    """Test that session start snapshots tracked changes with intent-to-add files."""
    repo = repo_with_changes

    # Create a tracked file and commit it
    tracked_file = repo / "tracked.py"
    tracked_file.write_text("# Original content\ndef old_func():\n    pass\n")
    subprocess.run(["git", "add", "tracked.py"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add tracked file"], check=True, capture_output=True)

    # Modify the tracked file
    tracked_file.write_text("# Modified content\ndef new_func():\n    return 42\n")

    # Create a new file and add it with intent-to-add
    new_file = repo / "new_file.py"
    new_file.write_text("# New file content\nclass NewClass:\n    pass\n")
    subprocess.run(["git", "add", "-N", "new_file.py"], check=True, capture_output=True)

    # Verify git stash create would fail in this state
    stash_test = subprocess.run(["git", "stash", "create"], capture_output=True, text=True)
    if stash_test.returncode != 0:
        print("Expected: git stash create fails with intent-to-add files")
        print(f"Error: {stash_test.stderr}")
        assert "not uptodate" in stash_test.stderr.lower() or "cannot" in stash_test.stderr.lower()

    # Start session - should handle this gracefully
    git_stage_batch("start")

    # Verify stash was created by checking if abort-stash file exists
    stash_file = Path(".git/git-stage-batch/abort-stash")

    if not stash_file.exists():
        snapshot_list = Path(".git/git-stage-batch/abort-snapshot-list")
        if snapshot_list.exists():
            snapshots = snapshot_list.read_text().strip().split('\n')
            print(f"Snapshots created: {snapshots}")

        raise AssertionError(
            "no stash was created\n"
            "Intent-to-add files in index prevent 'git stash create' from succeeding.\n"
            "This means modified tracked files won't be captured correctly in batch sources."
        )

    # Stash was created successfully
    stash_sha = stash_file.read_text().strip()
    assert stash_sha, "Stash SHA should not be empty"

    # Verify stash contains the modified tracked file
    stash_show = subprocess.run(
        ["git", "show", stash_sha, "--name-only"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "tracked.py" in stash_show.stdout, "Stash should contain modified tracked file"

    # Verify stash has the modified content (not original)
    stash_content = subprocess.run(
        ["git", "show", f"{stash_sha}:tracked.py"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "new_func" in stash_content.stdout, "Stash should have modified content"
    assert "return 42" in stash_content.stdout, "Stash should have new function body"


def test_batch_source_from_stashed_tracked_file(repo_with_changes):
    """Test that batch sources for modified tracked files use stash content."""
    repo = repo_with_changes

    # Create and commit a tracked file
    tracked_file = repo / "module.py"
    tracked_file.write_text("# Version 1\ndef version_one():\n    pass\n")
    subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add module"], check=True, capture_output=True)

    # Modify it
    tracked_file.write_text("# Version 2\ndef version_two():\n    return 'new'\n")

    # Add an intent-to-add file alongside the tracked modification.
    new_file = repo / "another.py"
    new_file.write_text("def another():\n    pass\n")
    subprocess.run(["git", "add", "-N", "another.py"], check=True, capture_output=True)

    # Start session
    git_stage_batch("start")

    # Navigate to tracked file
    for _ in range(20):
        show = git_stage_batch("show", check=False)
        if show.returncode != 0:
            break
        if "module.py" in show.stdout:
            break
        git_stage_batch("skip")

    # Create batch and discard
    git_stage_batch("new", "test-batch")
    git_stage_batch("discard", "--file", "--to", "test-batch")

    # Check batch source commit has the modified content (from stash)

    metadata_file = Path(".git/git-stage-batch/batches/test-batch/metadata.json")
    metadata = json.loads(metadata_file.read_text())

    batch_source_sha = metadata["files"]["module.py"]["batch_source_commit"]

    # Read batch source content
    result = subprocess.run(
        ["git", "show", f"{batch_source_sha}:module.py"],
        capture_output=True,
        text=True,
        check=True
    )
    batch_source_content = result.stdout

    assert "version_two" in batch_source_content, (
        f"batch source has wrong content\n"
        f"Expected: Modified content from working tree at session start (Version 2)\n"
        f"Got: {batch_source_content}"
    )
    assert "return 'new'" in batch_source_content, "Batch source should have new function body"
    assert "version_one" not in batch_source_content, "Batch source should not have old content"
