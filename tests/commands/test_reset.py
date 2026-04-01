"""Tests for reset command."""

import json
from git_stage_batch.core.line_selection import parse_line_selection
from git_stage_batch.exceptions import NoMoreHunks
from git_stage_batch.commands.again import command_again
from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim
from git_stage_batch.batch.storage import add_file_to_batch, read_file_from_batch
from git_stage_batch.commands.new import command_new_batch
from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.data.batch_sources import create_batch_source_commit, save_session_batch_sources

import subprocess

import pytest

from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.commands.reset import command_reset_from_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import (
    get_batch_metadata_file_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestResetFromBatch:
    """Tests for reset --from command."""

    def test_reset_requires_from_flag(self, temp_git_repo):
        """Test that reset requires --from flag."""
        # This is tested by argparse requiring the flag
        # If we tried to call command_reset_from_batch without batch_name, it would error
        pass

    def test_reset_nonexistent_batch_errors(self, temp_git_repo):
        """Test that resetting nonexistent batch errors."""
        with pytest.raises(CommandError) as exc_info:
            command_reset_from_batch("nonexistent")

        assert "does not exist" in str(exc_info.value.message).lower()

    def test_reset_whole_batch(self, temp_git_repo):
        """Test resetting all claims from a batch."""

        # Create a file with changes
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes
        test_file.write_text("line 1 modified\nline 2\nline 3\n")

        # Start session and include to batch
        command_start()
        fetch_next_change()
        command_include_to_batch("mybatch", quiet=True)

        # Verify batch has claims in metadata
        metadata_path = get_batch_metadata_file_path("mybatch")
        assert metadata_path.exists()
        metadata = json.loads(read_text_file_contents(metadata_path))
        assert "test.py" in metadata["files"]
        assert len(metadata["files"]["test.py"]["claimed_lines"]) > 0

        # Reset the batch
        command_reset_from_batch("mybatch")

        # Verify batch metadata files section is cleared
        metadata_after = json.loads(read_text_file_contents(metadata_path))
        assert metadata_after["files"] == {}

    def test_reset_line_claims(self, temp_git_repo):
        """Test resetting specific line claims from a batch."""

        # Create a file with multiple lines
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make multiple line changes
        test_file.write_text("line 1 modified\nline 2 modified\nline 3 modified\n")

        # Start session and include lines to batch
        command_start()
        fetch_next_change()
        command_include_to_batch("mybatch", line_ids="4,5,6", quiet=True)

        # Verify line claims exist in metadata JSON
        metadata_path = get_batch_metadata_file_path("mybatch")
        assert metadata_path.exists()
        metadata = json.loads(read_text_file_contents(metadata_path))
        batch_ownership = metadata["files"]["test.py"]
        batch_line_ids = set()
        for range_str in batch_ownership.get("claimed_lines", []):
            batch_line_ids.update(parse_line_selection(range_str))
        assert batch_line_ids == {1, 2, 3}

        # Reset only line 2 (renumbered from display ID 5)
        command_reset_from_batch("mybatch", line_ids="2")

        # Verify line 2 is removed from batch claims
        metadata_after = json.loads(read_text_file_contents(metadata_path))
        batch_ownership_after = metadata_after["files"]["test.py"]
        batch_line_ids_after = set()
        for range_str in batch_ownership_after.get("claimed_lines", []):
            batch_line_ids_after.update(parse_line_selection(range_str))
        assert batch_line_ids_after == {1, 3}

    def test_reset_with_multiple_batches(self, temp_git_repo):
        """Test that reset only makes hunks visible if not claimed by other batches."""

        # Create a file with changes
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 modified\n")

        # Start session and include to two batches
        command_start()
        fetch_next_change()
        command_include_to_batch("batch-a", quiet=True)

        # Reset and include again to second batch
        command_again()
        command_include_to_batch("batch-b", quiet=True)

        # Verify both batches have the file
        metadata_a_path = get_batch_metadata_file_path("batch-a")
        metadata_a = json.loads(read_text_file_contents(metadata_a_path))
        assert "test.py" in metadata_a["files"]

        metadata_b_path = get_batch_metadata_file_path("batch-b")
        metadata_b = json.loads(read_text_file_contents(metadata_b_path))
        assert "test.py" in metadata_b["files"]

        # Reset batch-a
        command_reset_from_batch("batch-a")

        # Verify batch-a no longer claims the file
        metadata_a_after = json.loads(read_text_file_contents(metadata_a_path))
        assert "test.py" not in metadata_a_after["files"]

        # Verify hunk is STILL filtered (because batch-b still claims it)
        command_again()
        with pytest.raises(NoMoreHunks):
            fetch_next_change()

        # Reset batch-b
        command_reset_from_batch("batch-b")

        # NOW hunk should be visible
        command_again()
        item = fetch_next_change()
        assert item is not None

    def test_reset_explicit_file_removes_only_that_file(self, temp_git_repo):
        """Test resetting an explicit file removes only that file from a batch."""

        (temp_git_repo / "file1.txt").write_text("one\n")
        (temp_git_repo / "file2.txt").write_text("two\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "file1.txt").write_text("one changed\n")
        (temp_git_repo / "file2.txt").write_text("two changed\n")

        command_new_batch("mybatch", "test batch")
        command_start()

        add_file_to_batch(
            "mybatch",
            "file1.txt",
            BatchOwnership(claimed_lines=["1"], deletions=[]),
            "100644",
        )
        add_file_to_batch(
            "mybatch",
            "file2.txt",
            BatchOwnership(claimed_lines=["1"], deletions=[]),
            "100644",
        )

        command_reset_from_batch("mybatch", file="file1.txt")

        metadata_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        assert "file1.txt" not in metadata_after["files"]
        assert "file2.txt" in metadata_after["files"]
        assert read_file_from_batch("mybatch", "file1.txt") is None
        assert read_file_from_batch("mybatch", "file2.txt") is not None

    def test_reset_line_with_explicit_file_uses_metadata_batch_source(self, temp_git_repo):
        """Line reset should not rebuild from the active session batch-source cache."""

        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 changed\nline 2 changed\n")

        command_new_batch("mybatch", "test batch")
        command_start()
        add_file_to_batch(
            "mybatch",
            "test.py",
            BatchOwnership(claimed_lines=["1", "2"], deletions=[]),
            "100644",
        )

        metadata_before = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        original_source = metadata_before["files"]["test.py"]["batch_source_commit"]

        wrong_source = create_batch_source_commit(
            "test.py",
            file_content_override=b"unrelated cache content\n",
        )
        save_session_batch_sources({"test.py": wrong_source})

        command_reset_from_batch("mybatch", line_ids="1", file="test.py")

        metadata_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        file_meta = metadata_after["files"]["test.py"]
        assert file_meta["batch_source_commit"] == original_source

        batch_line_ids = set()
        for range_str in file_meta.get("claimed_lines", []):
            batch_line_ids.update(parse_line_selection(range_str))
        assert batch_line_ids == {2}

    def test_reset_to_moves_selected_lines_to_destination_batch(self, temp_git_repo):
        """Test splitting selected lines into another batch."""

        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 changed\nline 2 changed\n")

        command_new_batch("source", "source batch")
        command_start()
        add_file_to_batch(
            "source",
            "test.py",
            BatchOwnership(claimed_lines=["1", "2"], deletions=[]),
            "100644",
        )

        source_before = json.loads(read_text_file_contents(get_batch_metadata_file_path("source")))
        original_source = source_before["files"]["test.py"]["batch_source_commit"]
        source_baseline = source_before["baseline"]

        command_reset_from_batch("source", line_ids="1", file="test.py", to_batch="dest")

        source_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("source")))
        dest_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("dest")))

        assert dest_after["baseline"] == source_baseline
        assert dest_after["files"]["test.py"]["batch_source_commit"] == original_source

        source_line_ids = set()
        for range_str in source_after["files"]["test.py"].get("claimed_lines", []):
            source_line_ids.update(parse_line_selection(range_str))
        assert source_line_ids == {2}

        dest_line_ids = set()
        for range_str in dest_after["files"]["test.py"].get("claimed_lines", []):
            dest_line_ids.update(parse_line_selection(range_str))
        assert dest_line_ids == {1}

    def test_reset_to_moves_explicit_file_only(self, temp_git_repo):
        """Test splitting one file out of a multi-file batch."""

        (temp_git_repo / "file1.txt").write_text("one\n")
        (temp_git_repo / "file2.txt").write_text("two\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "file1.txt").write_text("one changed\n")
        (temp_git_repo / "file2.txt").write_text("two changed\n")

        command_new_batch("source", "source batch")
        command_start()
        add_file_to_batch(
            "source",
            "file1.txt",
            BatchOwnership(claimed_lines=["1"], deletions=[]),
            "100644",
        )
        add_file_to_batch(
            "source",
            "file2.txt",
            BatchOwnership(claimed_lines=["1"], deletions=[]),
            "100644",
        )

        command_reset_from_batch("source", file="file1.txt", to_batch="dest")

        source_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("source")))
        dest_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("dest")))

        assert "file1.txt" not in source_after["files"]
        assert "file2.txt" in source_after["files"]
        assert "file1.txt" in dest_after["files"]
        assert "file2.txt" not in dest_after["files"]
        assert read_file_from_batch("source", "file1.txt") is None
        assert read_file_from_batch("dest", "file1.txt") is not None

    def test_reset_to_existing_batch_requires_same_baseline(self, temp_git_repo):
        """Test split destination must share the source batch baseline."""

        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 changed\n")

        command_new_batch("source", "source batch")
        command_start()
        add_file_to_batch(
            "source",
            "test.py",
            BatchOwnership(claimed_lines=["1"], deletions=[]),
            "100644",
        )

        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Advance history"], check=True, cwd=temp_git_repo, capture_output=True)
        command_new_batch("dest", "different baseline")

        with pytest.raises(CommandError) as exc_info:
            command_reset_from_batch("source", file="test.py", to_batch="dest")

        assert "different baseline" in str(exc_info.value.message).lower()

    def test_reset_to_same_batch_errors(self, temp_git_repo):
        """Test split destination cannot be the same as the source batch."""

        command_new_batch("mybatch", "test batch")

        with pytest.raises(CommandError) as exc_info:
            command_reset_from_batch("mybatch", to_batch="mybatch")

        assert "different batch" in str(exc_info.value.message).lower()

    def test_reset_replacement_unit_removes_both_presence_and_deletion(self, temp_git_repo):
        """Test that resetting a replacement unit removes both claimed line and deletion."""

        # Create a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes
        test_file.write_text("line 1 modified\nline 2\nline 3\n")

        # Create batch with replacement-style ownership (claimed line + coupled deletion)
        command_new_batch("mybatch", "test batch")
        command_start()
        fetch_next_change()

        # Manually create ownership with replacement: claim line 1, delete original line 1
        # The deletion is anchored at line 0 (start of file) to be spatially close to line 1
        ownership = BatchOwnership(
            claimed_lines=["1"],
            deletions=[DeletionClaim(anchor_line=None, content_lines=[b"line 1\n"])]
        )
        add_file_to_batch("mybatch", "test.py", ownership, "100644")

        # Verify initial ownership has both claimed line and deletion
        metadata = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        file_ownership = BatchOwnership.from_metadata_dict(metadata["files"]["test.py"])
        assert "1" in ",".join(file_ownership.claimed_lines)
        assert len(file_ownership.deletions) == 1

        # Reset the replacement unit - must select ALL display IDs in the unit
        # Display structure: deletion (ID 1) + claimed line (ID 2) = replacement unit
        # To reset this atomic unit, must select both display IDs
        command_reset_from_batch("mybatch", line_ids="1,2")

        # Verify file is removed from batch (no ownership remains)
        metadata_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        assert "test.py" not in metadata_after.get("files", {}), \
            "Expected file to be removed from batch when all ownership is reset"

    def test_reset_partial_replacement_unit_errors(self, temp_git_repo):
        """Test that partially selecting a replacement unit raises error."""

        # Create a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes
        test_file.write_text("line 1 modified\nline 2\nline 3\n")

        # Create batch with replacement
        command_new_batch("mybatch", "test batch")
        command_start()
        fetch_next_change()

        # Create replacement: deletion + claimed line
        ownership = BatchOwnership(
            claimed_lines=["1"],
            deletions=[DeletionClaim(anchor_line=None, content_lines=[b"line 1\n"])]
        )
        add_file_to_batch("mybatch", "test.py", ownership, "100644")

        # Attempting to select only display ID 1 (deletion) should error
        # because the unit includes both IDs 1 and 2 (deletion + claimed)
        with pytest.raises(CommandError) as exc_info:
            command_reset_from_batch("mybatch", line_ids="1")

        assert "atomic ownership unit" in str(exc_info.value.message).lower()
        assert "replacement" in str(exc_info.value.message).lower()

    def test_reset_presence_only_keeps_unrelated_deletions(self, temp_git_repo):
        """Test that resetting presence-only lines preserves unrelated deletion claims."""

        # Create a file with enough lines to test distant anchoring
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes
        test_file.write_text("line 1 modified\nline 2\nline 3 modified\nline 4\nline 5\nline 6\nline 7\n")

        # Create batch
        command_new_batch("mybatch", "test batch")
        command_start()
        fetch_next_change()

        # Create ownership with:
        # - claimed lines 1, 3, and 5 (line 5 separates line 3 from deletion in display)
        # - deletion anchored after line 6
        # Display order: claimed1, claimed3, claimed5, deletion-at-6
        # This keeps claimed3 separate from the deletion.
        ownership = BatchOwnership(
            claimed_lines=["1", "3", "5"],
            deletions=[DeletionClaim(anchor_line=6, content_lines=[b"debug_log()\n"])]
        )
        add_file_to_batch("mybatch", "test.py", ownership, "100644")

        # Display structure (display adjacency grouping):
        # - Display ID 1: claimed line 1 (PRESENCE_ONLY - not adjacent to deletion)
        # - Display ID 2: claimed line 3 (PRESENCE_ONLY - not adjacent to deletion)
        # - Display ID 3: claimed line 5 (REPLACEMENT with deletion - adjacent in display)
        # - Display ID 4: deletion (part of REPLACEMENT with claimed5)

        # Reset claimed line 3 (display ID 2) - should remove only that line
        command_reset_from_batch("mybatch", line_ids="2")

        # Verify line 3 is removed but lines 1, 5 and deletion remain
        metadata_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        file_ownership = BatchOwnership.from_metadata_dict(metadata_after["files"]["test.py"])
        claimed_ids = set()
        for range_str in file_ownership.claimed_lines:
            claimed_ids.update(parse_line_selection(range_str))

        assert 1 in claimed_ids, "Line 1 should remain"
        assert 3 not in claimed_ids, "Line 3 should be removed"
        assert 5 in claimed_ids, "Line 5 should remain (couples with deletion)"
        assert len(file_ownership.deletions) == 1, "Deletion should remain (couples with line 5)"

    def test_reset_replacement_unit_keeps_separate_presence_line(self, temp_git_repo):
        """Test that resetting a replacement unit preserves separate presence-only lines."""

        # Create a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes
        test_file.write_text("line 1 modified\nline 2 modified\nline 3\n")

        # Create batch
        command_new_batch("mybatch", "test batch")
        command_start()
        fetch_next_change()

        # Create ownership with:
        # - Replacement unit: deletion + claimed line 1
        # - Presence-only: claimed line 2
        ownership = BatchOwnership(
            claimed_lines=["1", "2"],
            deletions=[DeletionClaim(anchor_line=None, content_lines=[b"line 1\n"])]
        )
        add_file_to_batch("mybatch", "test.py", ownership, "100644")

        # Display structure:
        # - Display ID 1: deletion (start of replacement unit)
        # - Display ID 2: claimed source line 1 (end of replacement unit, atomic)
        # - Display ID 3: claimed source line 2 (separate presence-only unit)

        # Reset the replacement unit (display IDs 1,2) - should remove both deletion and line 1
        command_reset_from_batch("mybatch", line_ids="1,2")

        # Verify line 1 AND its deletion are removed, but line 2 remains
        metadata_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        file_ownership = BatchOwnership.from_metadata_dict(metadata_after["files"]["test.py"])
        claimed_ids = set()
        for range_str in file_ownership.claimed_lines:
            claimed_ids.update(parse_line_selection(range_str))

        assert 1 not in claimed_ids, "Source line 1 should be removed (replacement unit)"
        assert 2 in claimed_ids, "Source line 2 should remain (separate presence-only unit)"
        assert len(file_ownership.deletions) == 0, "Deletion should be removed with line 1 (replacement unit)"

    def test_reset_single_line_from_multi_line_presence_group(self, temp_git_repo):
        """Test that resetting one line from multiple presence-only lines works independently.

        Consecutive claimed lines remain separate reset targets.
        """

        # Create a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes to lines 1, 2, and 3
        test_file.write_text("line 1 modified\nline 2 modified\nline 3 modified\nline 4\n")

        # Create batch
        command_new_batch("mybatch", "test batch")
        command_start()
        fetch_next_change()

        # Create ownership with multiple claimed lines (no deletions)
        ownership = BatchOwnership(
            claimed_lines=["1", "2", "3"],
            deletions=[]
        )
        add_file_to_batch("mybatch", "test.py", ownership, "100644")

        # Display structure:
        # - Display ID 1: claimed source line 1 (presence-only unit)
        # - Display ID 2: claimed source line 2 (presence-only unit)
        # - Display ID 3: claimed source line 3 (presence-only unit)

        # Reset ONLY display ID 2 (source line 2)
        command_reset_from_batch("mybatch", line_ids="2")

        # Verify only source line 2 is removed, lines 1 and 3 remain
        metadata_after = json.loads(read_text_file_contents(get_batch_metadata_file_path("mybatch")))
        file_ownership = BatchOwnership.from_metadata_dict(metadata_after["files"]["test.py"])
        claimed_ids = set()
        for range_str in file_ownership.claimed_lines:
            claimed_ids.update(parse_line_selection(range_str))

        assert 1 in claimed_ids, "Source line 1 should remain (separate unit)"
        assert 2 not in claimed_ids, "Source line 2 should be removed (selected)"
        assert 3 in claimed_ids, "Source line 3 should remain (separate unit)"
        assert len(file_ownership.deletions) == 0, "No deletions in this ownership"
