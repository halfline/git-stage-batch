"""Test that batch source commits capture content at session start, not at first discard."""

import subprocess

from .conftest import git_stage_batch


def test_batch_source_captures_session_start_content(repo_with_changes):
    """Verify batch source commit has content from session start, not from after discard.

    This reproduces the bug where batch source commits were created lazily after
    discard operations, capturing the wrong (empty) content instead of the content
    that existed at session start.
    """
    repo = repo_with_changes

    # Create a new file with specific content
    new_file = repo / "test_module.py"
    original_content = """# Test module
from batch.display import display_func
from batch.match import match_func

def my_function():
    pass
"""
    new_file.write_text(original_content)

    # Add to git index (intent-to-add)
    subprocess.run(["git", "add", "-N", "test_module.py"], check=True, capture_output=True)

    # Start session - this should snapshot the file
    git_stage_batch("start")

    # Navigate to our file
    for _ in range(20):
        show = git_stage_batch("show", check=False)
        if show.returncode != 0:
            break
        if "test_module.py" in show.stdout:
            break
        git_stage_batch("skip")

    # Create batch
    git_stage_batch("new", "test-batch")

    # Discard the file - this removes it from working tree
    git_stage_batch("discard", "--file", "--to", "test-batch")

    # File should be gone from working tree
    assert not new_file.exists(), "File should be removed from working tree"

    # Now verify the batch source commit contains the ORIGINAL content
    # Read the batch metadata to get batch source commit
    import json
    from pathlib import Path

    state_dir = Path(".git/git-stage-batch")
    metadata_file = state_dir / "batches" / "test-batch" / "metadata.json"
    assert metadata_file.exists(), "Batch metadata should exist"

    metadata = json.loads(metadata_file.read_text())
    assert "files" in metadata, "Metadata should have files key"
    assert "test_module.py" in metadata["files"], "File should be in metadata"

    batch_source_commit = metadata["files"]["test_module.py"]["batch_source_commit"]
    assert batch_source_commit, "Batch source commit should exist"

    # Read content from batch source commit
    result = subprocess.run(
        ["git", "show", f"{batch_source_commit}:test_module.py"],
        capture_output=True,
        text=True,
        check=True
    )
    batch_source_content = result.stdout

    # CRITICAL: Batch source should have the imports that were in the file at session start
    assert "from batch.display import display_func" in batch_source_content, (
        f"BUG: Batch source commit doesn't have original content!\n"
        f"Expected imports from session start\n"
        f"Got: {batch_source_content[:200]}"
    )
    assert "from batch.match import match_func" in batch_source_content, (
        "BUG: Batch source missing second import"
    )
    assert "def my_function():" in batch_source_content, (
        "BUG: Batch source missing function definition"
    )
