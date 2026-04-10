"""Test that reset --from works correctly with the new metadata-based mask system."""

import json
import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch
from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.commands.reset import command_reset_from_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import find_and_cache_next_unblocked_hunk
from git_stage_batch.data.line_state import load_current_lines_from_state
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import get_processed_batch_ids_file_path


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


class TestResetWithNewMask:
    """Test reset command with metadata-based mask system."""

    def test_reset_clears_line_level_batch_from_mask(self, temp_git_repo):
        """Test that reset --from removes file from the new JSON mask."""
        # Create changes
        readme = temp_git_repo / "README"
        readme.write_text("# Test\nLine 1\nLine 2\n")

        # Start session and batch a line
        command_start()
        find_and_cache_next_unblocked_hunk()
        command_include_to_batch("batch-a", line_ids="2", quiet=True)

        # Verify file is in mask
        mask_path = get_processed_batch_ids_file_path()
        assert mask_path.exists()
        mask_content = read_text_file_contents(mask_path)
        file_mask = json.loads(mask_content)
        assert "README" in file_mask
        assert file_mask["README"]["claimed_lines"] == ["1"]  # Source line 1 (display line 2)

        # Verify line is masked (3 lines visible: 1 deletion + 2 remaining additions)
        find_and_cache_next_unblocked_hunk()
        current_lines = load_current_lines_from_state()
        changed_lines = [l for l in current_lines.lines if l.kind != " "]
        assert len(changed_lines) == 3  # Batched "# Test" is hidden, leaving deletion + 2 additions
        # Verify "# Test" is not visible
        assert all(l.text != "# Test" for l in changed_lines)

        # Reset the batch
        command_reset_from_batch("batch-a")

        # Verify file is removed from mask
        mask_content_after = read_text_file_contents(mask_path)
        file_mask_after = json.loads(mask_content_after)
        # Mask should be empty since we reset the only batch
        assert file_mask_after == {}

        # Verify all lines are now visible again (1 deletion + 3 additions)
        find_and_cache_next_unblocked_hunk()
        current_lines_after = load_current_lines_from_state()
        changed_lines_after = [l for l in current_lines_after.lines if l.kind != " "]
        assert len(changed_lines_after) == 4  # All lines visible
        # Verify "# Test" is now visible again
        assert any(l.text == "# Test" for l in changed_lines_after)

    def test_reset_with_multiple_batches_keeps_other_claims(self, temp_git_repo):
        """Test that reset only removes claims from one batch, not others."""
        # Create changes
        readme = temp_git_repo / "README"
        readme.write_text("# Test\nLine 1\nLine 2\n")

        # Start session
        command_start()

        # Batch line 2 ("# Test") to batch-a - this is source line 1
        find_and_cache_next_unblocked_hunk()
        command_include_to_batch("batch-a", line_ids="2", quiet=True)

        # After masking, display is: deletion, "Line 1" (id=2), "Line 2" (id=3)
        # Batch line 2 ("Line 1") to batch-b - this is source line 2
        find_and_cache_next_unblocked_hunk()
        command_include_to_batch("batch-b", line_ids="2", quiet=True)

        # Verify both source lines are in mask
        mask_path = get_processed_batch_ids_file_path()
        mask_content = read_text_file_contents(mask_path)
        file_mask = json.loads(mask_content)
        assert "README" in file_mask
        # Should have both source lines 1 and 2 (compressed as "1-2")
        from git_stage_batch.core.line_selection import parse_line_selection
        claimed = set(parse_line_selection(",".join(file_mask["README"]["claimed_lines"])))
        assert claimed == {1, 2}

        # Reset batch-a (removes source line 1)
        command_reset_from_batch("batch-a")

        # Verify only batch-b's claim remains (source line 2)
        mask_content_after = read_text_file_contents(mask_path)
        file_mask_after = json.loads(mask_content_after)
        assert "README" in file_mask_after
        assert file_mask_after["README"]["claimed_lines"] == ["2"]  # Only source line 2 from batch-b
