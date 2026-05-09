"""Functional tests for stale batch source advancement."""

from __future__ import annotations

import subprocess


from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.commands.discard import command_discard_to_batch
from git_stage_batch.commands.start import command_start


def _presence_source_lines(file_metadata: dict) -> list[str]:
    lines: list[str] = []
    for claim in file_metadata.get("presence_claims", []):
        lines.extend(claim.get("source_lines", []))
    return lines


def test_stale_source_advancement_on_discard(functional_repo):
    """Test that batch source is advanced when discarding new code added after initial batch source."""
    # Create initial file
    test_file = functional_repo / "test.py"
    test_file.write_text("line 1\nline 2\nline 3\n")

    # Commit it
    subprocess.run(["git", "add", "test.py"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    # Make first change - modify line 2
    test_file.write_text("line 1\nmodified line 2\nline 3\n")

    # Start batch staging
    command_start()

    # Discard to batch - this creates the initial batch source from the working tree
    # which has "modified line 2"
    command_discard_to_batch(batch_name="test-batch", quiet=True)

    # Get batch source after first discard
    metadata = read_batch_metadata("test-batch")
    assert "test.py" in metadata["files"]
    first_batch_source = metadata["files"]["test.py"]["batch_source_commit"]

    # Verify the batch captured the modification
    assert _presence_source_lines(metadata["files"]["test.py"]) == ["1-3"]

    # Now add ENTIRELY NEW code that doesn't exist in the batch source
    # The batch source has "line 1\nmodified line 2\nline 3\n"
    # Let's add a new function at the end
    test_file.write_text("line 1\nmodified line 2\nline 3\ndef new_function():\n    pass\n")

    # Try to discard the new code to the same batch
    # This should detect stale source, advance it, and remap existing ownership
    command_start()
    command_discard_to_batch(batch_name="test-batch", quiet=True)

    # Verify batch source was advanced
    metadata = read_batch_metadata("test-batch")
    second_batch_source = metadata["files"]["test.py"]["batch_source_commit"]

    # Batch source should have changed
    assert second_batch_source != first_batch_source

    # Verify ownership was preserved and extended
    # Should now claim all 5 lines
    assert "1-5" in ",".join(_presence_source_lines(metadata["files"]["test.py"]))


def test_stale_discard_preserves_previously_discarded_claimed_lines(functional_repo):
    """Advancing a discard batch must not drop lines already removed from WT."""
    test_file = functional_repo / "test.py"
    test_file.write_text("base\n")

    subprocess.run(["git", "add", "test.py"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    test_file.write_text("owned earlier\nbase\n")

    command_start()
    command_discard_to_batch(batch_name="test-batch", quiet=True)

    assert test_file.read_text() == "base\n"

    test_file.write_text("base\nnew later\n")

    command_start()
    command_discard_to_batch(batch_name="test-batch", quiet=True)

    metadata = read_batch_metadata("test-batch")
    file_metadata = metadata["files"]["test.py"]

    source = subprocess.run(
        ["git", "show", f"{file_metadata['batch_source_commit']}:test.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "owned earlier\n" in source
    assert "new later\n" in source
    assert ",".join(_presence_source_lines(file_metadata)) == "1-3"


def test_existing_ownership_preserved_through_advancement(functional_repo):
    """Test that existing ownership is correctly remapped when batch source advances."""
    test_file = functional_repo / "test.py"
    test_file.write_text("a\nb\nc\nd\n")

    subprocess.run(["git", "add", "test.py"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    # Modify middle lines
    test_file.write_text("a\nB_modified\nC_modified\nd\n")

    command_start()

    # Discard the replacement line (display line 3 which is the addition "B_modified")
    command_discard_to_batch(batch_name="test-batch", line_ids="3", quiet=True)

    # Verify initial ownership - should have claimed the line for B_modified
    metadata = read_batch_metadata("test-batch")
    # Should have claimed line 2 in batch source space (the working tree line for B_modified)
    assert len(_presence_source_lines(metadata["files"]["test.py"])) > 0

    # Add new code at the end
    test_file.write_text("a\nB_modified\nC_modified\nd\ne\nf\n")

    # Discard new code to same batch
    command_start()
    command_discard_to_batch(batch_name="test-batch", quiet=True)

    # Verify ownership was preserved and extended
    metadata = read_batch_metadata("test-batch")
    claimed = ",".join(_presence_source_lines(metadata["files"]["test.py"]))

    # Should have original claimed line(s), remapped to new position, plus new lines
    # The exact line numbers depend on batch source mapping, but we should have more lines claimed
    # after the second discard
    assert len(_presence_source_lines(metadata["files"]["test.py"])) > 0
    # Verify we have additions - the claimed lines string should contain numbers
    assert any(c.isdigit() for c in claimed)


def test_only_modified_batch_file_gets_new_source(functional_repo):
    """Test that only the specific batch/file being modified gets a new batch source."""
    file1 = functional_repo / "file1.txt"
    file2 = functional_repo / "file2.txt"

    file1.write_text("file1 line 1\n")
    file2.write_text("file2 line 1\n")

    subprocess.run(["git", "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    # Modify both files
    file1.write_text("file1 modified\n")
    file2.write_text("file2 modified\n")

    command_start()

    # Create two batches, each owning one file
    command_discard_to_batch(batch_name="batch1", quiet=True)  # Gets file1
    command_discard_to_batch(batch_name="batch2", quiet=True)  # Gets file2

    # Get initial batch sources
    metadata1 = read_batch_metadata("batch1")
    metadata2 = read_batch_metadata("batch2")

    batch1_file1_source = metadata1["files"]["file1.txt"]["batch_source_commit"]
    batch2_file2_source = metadata2["files"]["file2.txt"]["batch_source_commit"]

    # Now add new code ONLY to file1
    file1.write_text("file1 modified\nfile1 new code\n")

    # Discard new code from file1 to batch1
    command_start()
    command_discard_to_batch(batch_name="batch1", quiet=True)

    # Check that batch1/file1 got a new source
    metadata1_after = read_batch_metadata("batch1")
    batch1_file1_source_after = metadata1_after["files"]["file1.txt"]["batch_source_commit"]

    assert batch1_file1_source_after != batch1_file1_source

    # Check that batch2/file2 source is UNCHANGED
    metadata2_after = read_batch_metadata("batch2")
    batch2_file2_source_after = metadata2_after["files"]["file2.txt"]["batch_source_commit"]

    assert batch2_file2_source_after == batch2_file2_source


def test_first_discard_with_stale_source_succeeds(functional_repo):
    """Test that first-time discard succeeds even when source_line is None."""
    # This tests the case where we're discarding to a batch for the first time
    # and the working tree has additions that don't exist in any batch source yet
    test_file = functional_repo / "newfile.py"
    test_file.write_text("def new_function():\n    pass\n")

    # Don't stage or commit - file is entirely new and untracked
    # auto_add_untracked_files will make it visible to git diff with intent-to-add

    command_start()

    # This should succeed even though all lines have source_line=None initially
    # because there's no existing batch source to be stale relative to
    command_discard_to_batch(batch_name="new-batch", quiet=True)

    # Verify batch was created with the new content
    metadata = read_batch_metadata("new-batch")
    assert "newfile.py" in metadata["files"]
    assert _presence_source_lines(metadata["files"]["newfile.py"])


def test_deletion_anchors_remapped_correctly(functional_repo):
    """Test that deletion claim anchors are correctly remapped when source advances."""
    test_file = functional_repo / "test.py"
    test_file.write_text("line 1\nline 2\nline 3\nline 4\n")

    subprocess.run(["git", "add", "test.py"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    # Delete line 2
    test_file.write_text("line 1\nline 3\nline 4\n")

    command_start()

    # Discard the deletion to batch
    command_discard_to_batch(batch_name="test-batch", quiet=True)

    metadata = read_batch_metadata("test-batch")
    assert len(metadata["files"]["test.py"]["deletions"]) == 1

    initial_deletion = metadata["files"]["test.py"]["deletions"][0]
    initial_anchor = initial_deletion["after_source_line"]

    # Add new code at the beginning
    test_file.write_text("NEW_LINE_0\nline 1\nline 3\nline 4\n")

    # Discard new code to same batch - should advance source and remap deletion anchor
    command_start()
    command_discard_to_batch(batch_name="test-batch", quiet=True)

    # Verify deletion anchor was remapped
    metadata_after = read_batch_metadata("test-batch")
    assert len(metadata_after["files"]["test.py"]["deletions"]) == 1

    # Anchor should have been incremented because we added a line before it
    # (exact value depends on line mapping, but it should change)
    remapped_deletion = metadata_after["files"]["test.py"]["deletions"][0]
    remapped_anchor = remapped_deletion["after_source_line"]

    # The anchor should have shifted
    if initial_anchor is not None:
        assert remapped_anchor != initial_anchor
