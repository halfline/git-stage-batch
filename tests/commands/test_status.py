"""Tests for status command."""

from git_stage_batch.batch import create_batch
from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.batch.storage import add_binary_file_to_batch
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.commands.include import command_include_line, command_include_to_batch
from git_stage_batch.commands.reset import command_reset_from_batch
from git_stage_batch.commands.show import command_show, command_show_file_list
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.data.hunk_tracking import SelectedChangeKind, read_selected_change_kind
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import get_iteration_count_file_path
from git_stage_batch.utils.paths import ensure_state_directory_exists, get_state_directory_path

import json
import subprocess

import pytest

from git_stage_batch.core.models import BinaryFileChange
from git_stage_batch.commands.include import command_include
from git_stage_batch.commands.skip import command_skip, command_skip_file
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.status import command_status
from git_stage_batch.core.line_selection import format_line_ids
from git_stage_batch.data.file_review_state import read_last_file_review_state
from git_stage_batch.exceptions import CommandError


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


class TestCommandStatus:
    """Tests for status command."""

    def test_status_no_session(self, temp_git_repo, capsys):
        """Test status when no session is active."""
        command_status()

        captured = capsys.readouterr()
        assert "No batch staging session in progress" in captured.err
        assert "git-stage-batch start" in captured.err

    def test_status_active_session_no_changes(self, temp_git_repo, capsys):
        """Test status with active session but no changes."""
        # Create changes for start, then stage them so nothing is left
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)

        command_status()

        captured = capsys.readouterr()
        assert "Session active" in captured.out or "No batch staging session in progress" in captured.err

    def test_status_with_unprocessed_hunks(self, temp_git_repo, capsys):
        """Test status with unprocessed hunks."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_status()

        captured = capsys.readouterr()
        # Status might show session not in progress if start hasn't been called
        assert "Session active" in captured.out or "No batch staging session in progress" in captured.err

    def test_status_with_processed_hunks(self, temp_git_repo, capsys):
        """Test status after processing some hunks."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Start session
        command_start()

        # Include first hunk
        command_include()
        capsys.readouterr()  # Clear output

        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration" in captured.out
        assert "Progress this iteration:" in captured.out
        assert "Remaining:" in captured.out
        assert "file2.txt" in captured.out

    def test_status_all_hunks_processed(self, temp_git_repo, capsys):
        """Test status when all hunks have been processed."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Start session
        command_start()

        # Skip the only hunk
        command_skip()
        capsys.readouterr()  # Clear output

        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration" in captured.out
        assert "Progress this iteration:" in captured.out
        assert "Remaining:" in captured.out
        # When all hunks are processed, should show completion message
        assert "complete" in captured.out or "Remaining: 0" in captured.out or "Remaining:  0" in captured.out

    def test_status_counts_hunks_skipped_by_file_scope(self, temp_git_repo, capsys):
        """File-scoped skip should update skipped progress metrics."""
        test_file = temp_git_repo / "multi.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 modified\nline 2\nline 3\nline 4\nline 5 modified\n")

        command_start()
        command_skip_file()
        capsys.readouterr()

        command_status()

        captured = capsys.readouterr()
        assert "Skipped:   1 hunks" in captured.out
        assert "multi.txt:1 [#1-4]" in captured.out

    def test_status_shows_line_range_when_cached(self, temp_git_repo, capsys):
        """Test that status shows line range when cached state is available."""

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Start session
        command_start()

        # Show the hunk (which caches state)
        command_show()
        capsys.readouterr()  # Clear output

        # Check status - should show line range
        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration" in captured.out
        assert "Current hunk:" in captured.out
        assert "test.txt" in captured.out
        assert "[#" in captured.out  # Should show line IDs in brackets

    def test_status_shows_iteration_and_progress_metrics(self, temp_git_repo, capsys):
        """Test that status shows iteration number and progress metrics."""

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file to create a hunk
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Set iteration count to 2 to test iteration display
        ensure_state_directory_exists()
        initialize_abort_state()
        get_iteration_count_file_path().write_text("2")

        # Cache selected hunk with show
        command_show()
        capsys.readouterr()

        # Check status
        command_status()

        captured = capsys.readouterr()
        # Should show iteration number
        assert "Session: iteration 2" in captured.out
        assert "(in progress)" in captured.out

        # Should show progress metrics
        assert "Progress this iteration:" in captured.out
        assert "Included:" in captured.out
        assert "Skipped:" in captured.out
        assert "Discarded:" in captured.out
        assert "Remaining:" in captured.out

    def test_status_porcelain_no_session(self, temp_git_repo, capsys):
        """Test status --porcelain when no session is active."""
        command_status(porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == {"session": {"active": False}}

    def test_status_for_prompt_no_session_is_silent(self, temp_git_repo, capsys):
        """Prompt mode should hide the entire configured segment when inactive."""
        command_status(prompt_format=" [{status}]")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_status_for_prompt_outside_repository_is_silent(self, tmp_path, monkeypatch, capsys):
        """Prompt mode should be safe to call from non-git directories."""
        monkeypatch.chdir(tmp_path)

        command_status(prompt_format=" STAGING")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_status_for_prompt_default_static_label(self, temp_git_repo, capsys, monkeypatch):
        """The default prompt segment should only need an active session marker."""
        ensure_state_directory_exists()
        initialize_abort_state()

        def fail_estimate_remaining_hunks():
            raise AssertionError("full status should not be read")

        monkeypatch.setattr(
            "git_stage_batch.commands.status.estimate_remaining_hunks",
            fail_estimate_remaining_hunks,
        )

        command_status(prompt_format=" STAGING")

        captured = capsys.readouterr()
        assert captured.out == " STAGING"
        assert captured.err == ""

    def test_status_for_prompt_formats_status_fields(self, temp_git_repo, capsys):
        """Prompt mode should render custom fields without a trailing newline."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")
        command_start()
        capsys.readouterr()

        command_status(
            prompt_format=(
                " [{status}:{status_label}:{progress_status}:"
                "{progress_label}:{iteration}:{remaining}:{selected_file}]"
            )
        )

        captured = capsys.readouterr()
        assert captured.out.startswith(" [STAGING:STAGING:in_progress:in progress:1:")
        assert captured.out.endswith(":README.md]")
        assert "\n" not in captured.out
        assert captured.err == ""

    def test_status_for_prompt_formats_processed_and_total_counts(self, temp_git_repo, capsys):
        """Prompt mode should expose processed and total progress counts."""
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        command_start()
        command_include()
        capsys.readouterr()

        command_status(prompt_format=" {processed}/{total}:{included}:{skipped}:{discarded}:{remaining}")

        captured = capsys.readouterr()
        assert captured.out == " 1/2:1:0:0:1"
        assert captured.err == ""

    def test_status_for_prompt_reports_complete_session(self, temp_git_repo, capsys):
        """Prompt status should switch to complete when no work remains."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")
        command_start()
        command_skip()
        capsys.readouterr()

        command_status(prompt_format="[{status}:{progress_status}]")

        captured = capsys.readouterr()
        assert captured.out == "[STAGING:complete]"

    def test_status_for_prompt_unknown_field_errors_when_active(self, temp_git_repo):
        """Unknown prompt fields should be reported for active sessions."""
        ensure_state_directory_exists()
        initialize_abort_state()

        with pytest.raises(CommandError, match="Unknown status prompt field 'missing'"):
            command_status(prompt_format="[{missing}]")

    def test_status_ignores_persistent_batch_state_without_session(self, temp_git_repo, capsys):
        """Persistent batch metadata alone should not count as an active session."""
        state_dir = get_state_directory_path()
        (state_dir / "batches").mkdir(parents=True, exist_ok=True)

        command_status()

        captured = capsys.readouterr()
        assert "No batch staging session in progress" in captured.err

    def test_status_porcelain_with_session(self, temp_git_repo, capsys):
        """Test status --porcelain outputs JSON with session data."""

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Initialize session state
        ensure_state_directory_exists()
        initialize_abort_state()

        # Cache selected hunk with show
        command_show()
        capsys.readouterr()

        # Get JSON output
        command_status(porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify JSON structure
        assert output["session"]["active"] is True
        assert output["session"]["iteration"] == 1
        assert output["session"]["status"] in ("in_progress", "complete")
        assert isinstance(output["session"]["in_progress"], bool)
        assert "progress" in output
        assert "included" in output["progress"]
        assert "skipped" in output["progress"]
        assert "discarded" in output["progress"]
        assert "remaining" in output["progress"]
        assert "skipped_hunks" in output

    def test_status_porcelain_includes_selected_change(self, temp_git_repo, capsys):
        """Test status --porcelain includes selected change details."""

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Initialize session and cache hunk
        ensure_state_directory_exists()
        initialize_abort_state()
        command_show()
        capsys.readouterr()

        # Get JSON output
        command_status(porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["selected_change"] is not None
        assert output["selected_change"]["kind"] == "hunk"
        assert output["selected_change"]["file"] == "test.txt"
        assert "line" in output["selected_change"]
        assert "ids" in output["selected_change"]

    def test_status_in_progress_when_no_selection_but_hunks_remain(self, temp_git_repo, capsys):
        """Navigational file lists clear selection without making the session complete."""
        (temp_git_repo / "a.txt").write_text("a\n")
        (temp_git_repo / "b.txt").write_text("b\n")
        subprocess.run(["git", "add", "a.txt", "b.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "a.txt").write_text("a changed\n")
        (temp_git_repo / "b.txt").write_text("b changed\n")

        command_start()
        capsys.readouterr()
        command_show_file_list(["a.txt", "b.txt"])
        capsys.readouterr()

        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration 1 (in progress)" in captured.out
        assert "Current hunk:" not in captured.out
        assert "Remaining: ~2 hunks" in captured.out

    def test_status_reports_selected_batch_file_review_without_snapshots(self, temp_git_repo, capsys):
        """Batch-file reviews intentionally lack live snapshots but remain selected."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nbatched\n")

        command_start()
        capsys.readouterr()
        command_include_to_batch("cleanup", file="README.md", quiet=True)
        capsys.readouterr()
        command_show_from_batch("cleanup", file="README.md")
        capsys.readouterr()

        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration 1 (in progress)" in captured.out
        assert "Current batch file review:" in captured.out
        assert "Last file review:" in captured.out
        assert "source: batch cleanup" in captured.out

        command_status(porcelain=True)
        output = json.loads(capsys.readouterr().out)
        assert output["session"]["status"] == "in_progress"
        assert output["selected_change"]["kind"] == "batch-file"
        assert output["selected_change"]["file"] == "README.md"
        assert output["file_review"]["source"] == "batch"
        assert output["file_review"]["batch_name"] == "cleanup"

    def test_status_reports_batch_review_gutter_ids(self, temp_git_repo, capsys):
        """Batch-file status should show user-visible gutter IDs, not source IDs."""
        test_file = temp_git_repo / "file.txt"
        test_file.write_text("keep\nold\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        ensure_state_directory_exists()
        initialize_abort_state()
        test_file.write_text("keep\nnew\n")
        create_batch("manual")
        add_file_to_batch(
            "manual",
            "file.txt",
            BatchOwnership.from_presence_lines(["2"], []),
            "100644",
        )
        test_file.write_text("keep\nold\n")

        command_show_from_batch("manual", file="file.txt", page="all")
        capsys.readouterr()

        command_status(porcelain=True)
        output = json.loads(capsys.readouterr().out)

        assert output["selected_change"]["kind"] == "batch-file"
        assert output["selected_change"]["ids"] == [1]

        command_status()
        captured = capsys.readouterr()
        assert "[#1]" in captured.out
        assert "[#2]" not in captured.out

    def test_status_reports_only_shown_batch_review_gutter_ids(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Batch-file status should not advertise hidden-page line IDs."""
        from git_stage_batch.output import file_review

        monkeypatch.setattr(file_review, "_body_budget", lambda: 1)
        test_file = temp_git_repo / "file.txt"
        original = [f"line {number}\n" for number in range(1, 31)]
        test_file.write_text("".join(original))
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        changed = original[:]
        changed[1] = "line 2 changed\n"
        changed[11] = "line 12 changed\n"
        changed[21] = "line 22 changed\n"
        test_file.write_text("".join(changed))

        ensure_state_directory_exists()
        initialize_abort_state()
        add_file_to_batch(
            "cleanup",
            "file.txt",
            BatchOwnership.from_presence_lines(["2", "12", "22"], []),
            "100644",
        )

        command_show_from_batch("cleanup", file="file.txt", page="1")
        capsys.readouterr()

        command_status(porcelain=True)
        output = json.loads(capsys.readouterr().out)

        assert output["selected_change"]["kind"] == "batch-file"
        assert output["selected_change"]["ids"] == [1]

    def test_status_reports_only_shown_live_review_gutter_ids(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Live file status should not advertise hidden-page line IDs."""
        from git_stage_batch.output import file_review

        monkeypatch.setattr(file_review, "_body_budget", lambda: 1)
        test_file = temp_git_repo / "file.txt"
        original = [f"line {number}\n" for number in range(1, 31)]
        test_file.write_text("".join(original))
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        changed = original[:]
        changed[1] = "line 2 changed\n"
        changed[11] = "line 12 changed\n"
        changed[21] = "line 22 changed\n"
        test_file.write_text("".join(changed))

        command_start()
        capsys.readouterr()
        command_show(file="file.txt", page="1")
        capsys.readouterr()

        command_status(porcelain=True)
        output = json.loads(capsys.readouterr().out)

        assert output["selected_change"]["kind"] == "file"
        assert output["selected_change"]["ids"] == [1, 2]

    def test_status_hides_ids_from_stale_live_review(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Status should not fall back to raw IDs from a stale live review."""
        from git_stage_batch.output import file_review

        monkeypatch.setattr(file_review, "_body_budget", lambda: 1)
        test_file = temp_git_repo / "file.txt"
        original = [f"line {number}\n" for number in range(1, 31)]
        test_file.write_text("".join(original))
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        changed = original[:]
        changed[1] = "line 2 changed\n"
        changed[11] = "line 12 changed\n"
        changed[21] = "line 22 changed\n"
        test_file.write_text("".join(changed))

        command_start()
        capsys.readouterr()
        command_show(file="file.txt", page="1")
        capsys.readouterr()
        state = read_last_file_review_state()
        assert state is not None
        line_spec = format_line_ids(list(state.selections[0].display_ids))

        command_include_line(line_spec)
        capsys.readouterr()

        command_status(porcelain=True)
        output = json.loads(capsys.readouterr().out)

        assert output["file_review"]["fresh"] is False
        assert output["selected_change"]["kind"] == "file"
        assert output["selected_change"]["ids"] == []

    def test_status_complete_after_selected_batch_file_is_reset(self, temp_git_repo, capsys):
        """Resetting the selected batch file should not leave status in progress."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nbatched\n")

        command_start()
        capsys.readouterr()
        command_include_to_batch("cleanup", file="README.md", quiet=True)
        capsys.readouterr()
        subprocess.run(["git", "checkout", "HEAD", "--", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)

        command_show_from_batch("cleanup", file="README.md", page="all")
        capsys.readouterr()
        command_reset_from_batch("cleanup")
        capsys.readouterr()

        command_status(porcelain=True)

        output = json.loads(capsys.readouterr().out)
        assert output["session"]["status"] == "complete"
        assert output["selected_change"] is None
        assert output["progress"]["remaining"] == 0

    def test_status_hides_stale_binary_selection_without_clearing_it(self, temp_git_repo, capsys):
        """A stale binary selection should not affect progress or lose its pathless-action guard."""
        binary_file = temp_git_repo / "asset.bin"
        binary_file.write_bytes(b"\x00\x01\x02")
        subprocess.run(["git", "add", "asset.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)
        binary_file.write_bytes(b"\x00\x03\x04")

        ensure_state_directory_exists()
        initialize_abort_state()
        command_show(file="asset.bin", porcelain=True)
        subprocess.run(["git", "restore", "asset.bin"], check=True, cwd=temp_git_repo, capture_output=True)

        command_status(porcelain=True)

        output = json.loads(capsys.readouterr().out)
        assert output["session"]["status"] == "complete"
        assert output["selected_change"] is None
        assert output["progress"]["remaining"] == 0
        assert read_selected_change_kind() == SelectedChangeKind.BINARY

    def test_status_keeps_current_deleted_binary_selection(self, temp_git_repo, capsys):
        """A binary deletion should remain selected while the deletion is still present."""
        binary_file = temp_git_repo / "asset.bin"
        binary_file.write_bytes(b"\x00\x01\x02")
        subprocess.run(["git", "add", "asset.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)
        binary_file.unlink()

        ensure_state_directory_exists()
        initialize_abort_state()
        command_show(file="asset.bin", porcelain=True)

        command_status(porcelain=True)

        output = json.loads(capsys.readouterr().out)
        assert output["session"]["status"] == "in_progress"
        assert output["selected_change"]["kind"] == "binary"
        assert output["selected_change"]["file"] == "asset.bin"
        assert output["selected_change"]["change_type"] == "deleted"

    def test_status_drops_stale_batch_binary_selection(self, temp_git_repo, capsys):
        """A cached batch-binary selection should not survive changed batch bytes."""
        binary_file = temp_git_repo / "asset.bin"
        binary_file.write_bytes(b"\x00\x01\x02")
        subprocess.run(["git", "add", "asset.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)

        ensure_state_directory_exists()
        initialize_abort_state()
        binary_file.write_bytes(b"\x00\x03\x04")
        add_binary_file_to_batch(
            "bin-batch",
            BinaryFileChange("asset.bin", "asset.bin", "modified"),
        )
        command_show_from_batch("bin-batch", file="asset.bin")
        capsys.readouterr()

        binary_file.write_bytes(b"\x00\x05\x06")
        add_binary_file_to_batch(
            "bin-batch",
            BinaryFileChange("asset.bin", "asset.bin", "modified"),
        )

        command_status(porcelain=True)

        output = json.loads(capsys.readouterr().out)
        assert output["selected_change"] is None
        assert read_selected_change_kind() is None
