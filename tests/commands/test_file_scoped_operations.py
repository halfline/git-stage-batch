"""Comprehensive tests for file-scoped operations with --file flag.

These tests define the desired functionality for --file operations.
If tests fail, they expose gaps in the implementation.
"""

from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import ensure_state_directory_exists
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.data.line_state import load_line_changes_from_state
from git_stage_batch.core.hashing import compute_stable_hunk_hash_from_lines
from git_stage_batch.utils.paths import (
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)
from git_stage_batch.data.hunk_tracking import read_selected_change_kind, render_unstaged_file_as_single_hunk
from git_stage_batch.commands.skip import command_skip, command_skip_line
from git_stage_batch.cli.argument_parser import parse_command_line

import subprocess

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.show import command_show
from git_stage_batch.commands.include import (
    command_include,
    command_include_to_batch,
    command_include_file,
    command_include_file_as,
    command_include_line,
    command_include_line_as,
)
from git_stage_batch.commands.discard import command_discard, command_discard_to_batch, command_discard_file, command_discard_file_as, command_discard_line
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import run_git_command


@pytest.fixture
def multi_file_repo(tmp_path, monkeypatch):
    """Create a repo with multiple modified files."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    # Initialize git
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

    # Create multiple files with distinct content
    (repo / "alpha.txt").write_text("alpha1\nalpha2\nalpha3\n")
    (repo / "beta.txt").write_text("beta1\nbeta2\nbeta3\n")
    (repo / "gamma.txt").write_text("gamma1\ngamma2\ngamma3\n")
    subprocess.run(["git", "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    # Modify all files with distinct patterns
    (repo / "alpha.txt").write_text("alpha1\nalpha2-modified\nalpha3\nalpha4-new\n")
    (repo / "beta.txt").write_text("beta1\nbeta2-modified\nbeta3\nbeta4-new\n")
    (repo / "gamma.txt").write_text("gamma1\ngamma2-modified\ngamma3\ngamma4-new\n")

    return repo


class TestShowFileFlag:
    """Test show command with --file flag for displaying entire files."""

    def test_show_file_with_path_displays_entire_file(self, multi_file_repo, capsys):
        """Show --file PATH should display all changes from specified file."""
        command_start()
        capsys.readouterr()  # Clear start's output
        command_show(file="beta.txt")

        captured = capsys.readouterr()
        assert "beta.txt" in captured.out
        assert "beta2-modified" in captured.out
        assert "beta4-new" in captured.out
        # Should show only the requested file.
        assert "alpha" not in captured.out.lower()
        assert "gamma" not in captured.out.lower()

    def test_show_file_empty_string_uses_selected_hunk_file(self, multi_file_repo, capsys):
        """Show --file (no path) should use selected hunk's file."""
        command_start()
        capsys.readouterr()  # Clear start's output
        # Current hunk is from alpha.txt (first file alphabetically)
        command_show(file="")

        captured = capsys.readouterr()
        assert "alpha.txt" in captured.out
        assert "alpha2-modified" in captured.out
        assert "alpha4-new" in captured.out

    def test_show_file_requires_active_session(self, multi_file_repo):
        """Show --file should fail if no session active."""
        # No session started
        with pytest.raises(CommandError, match="No session in progress"):
            command_show(file="alpha.txt")

    def test_show_file_empty_string_requires_selected_hunk(self, multi_file_repo):
        """Show --file (empty) should fail if no selected hunk cached."""

        # Start session but don't cache a hunk
        ensure_state_directory_exists()
        initialize_abort_state()

        with pytest.raises(CommandError, match="No selected hunk"):
            command_show(file="")

    def test_show_file_with_no_changes(self, multi_file_repo, capsys):
        """Show --file on unchanged file should report no changes."""
        command_start()
        capsys.readouterr()  # Clear start's output
        # Create unchanged file
        (multi_file_repo / "unchanged.txt").write_text("same\n")
        subprocess.run(["git", "add", "unchanged.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add"], check=True, capture_output=True)

        command_show(file="unchanged.txt")

        captured = capsys.readouterr()
        assert "No changes" in captured.err

    def test_show_file_selection_can_feed_include_line(self, multi_file_repo):
        """Show --file should cache a usable selection for later include --line."""
        command_start()
        command_show(file="beta.txt")

        command_include_line("3")

        result = run_git_command(["show", ":beta.txt"])
        assert result.stdout == "beta1\nbeta2\nbeta3\nbeta4-new\n"

    def test_explicit_file_include_line_rebuilds_stale_same_file_view(self, tmp_path, monkeypatch):
        """Explicit --file line IDs apply to the current file view, not stale state."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        file_path = repo / "file.txt"
        file_path.write_text("base\n")
        subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        file_path.write_text("base\nselected\n")
        command_start()
        command_show(file="file.txt")

        file_path.write_text("base\nother\n")
        command_include_line("1", file="file.txt")

        result = run_git_command(["show", ":file.txt"])
        assert result.stdout == "base\nother\n"

    def test_show_file_selection_can_feed_discard_line(self, multi_file_repo):
        """Show --file should cache a usable selection for later discard --line."""
        command_start()
        command_show(file="beta.txt")

        command_discard_line("3")

        assert (multi_file_repo / "beta.txt").read_text() == "beta1\nbeta2-modified\nbeta3\n"

    def test_show_file_include_line_keeps_file_selection_for_multi_hunk_file(self, tmp_path, monkeypatch):
        """Include --line from a file selection should keep the remaining file view."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 121)]
        (repo / "multi.txt").write_text("".join(lines))
        subprocess.run(["git", "add", "multi.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        content = lines[:]
        content.insert(5, "change-one\n")
        content.insert(55, "change-two\n")
        content.insert(105, "change-three\n")
        (repo / "multi.txt").write_text("".join(content))

        command_start()
        command_show(file="multi.txt")

        command_include_line("1")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        changed_texts = [line.display_text() for line in line_changes.lines if line.kind != " "]
        assert "change-one" not in changed_texts
        assert "change-two" in changed_texts
        assert "change-three" in changed_texts

    def test_explicit_file_include_line_uses_remaining_unstaged_diff(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line should not rebase on HEAD after partial staging."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 81)]
        lines[0] = "from old_alpha import thing\n"
        lines[10] = "from old_beta import helper\n"
        lines[60] = "body_call_old()\n"
        file_path = repo / "module.py"
        file_path.write_text("".join(lines))
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        rewritten = lines[:]
        rewritten[0] = "from new_alpha import thing\n"
        rewritten[10] = "from new_beta import helper\n"
        rewritten[60] = "body_call_new()\n"
        file_path.write_text("".join(rewritten))

        command_start()

        def ids_for_texts(*texts: str) -> str:
            command_show(file="module.py")
            line_changes = load_line_changes_from_state()
            assert line_changes is not None
            selected_ids = [
                str(line.id)
                for line in line_changes.lines
                if line.id is not None and line.display_text() in texts
            ]
            assert len(selected_ids) == len(texts)
            return ",".join(selected_ids)

        body_ids = ids_for_texts("body_call_old()", "body_call_new()")
        command_include_line(body_ids, file="module.py")

        alpha_ids = ids_for_texts(
            "from old_alpha import thing",
            "from new_alpha import thing",
        )
        command_include_line(alpha_ids, file="module.py")

        beta_ids = ids_for_texts(
            "from old_beta import helper",
            "from new_beta import helper",
        )
        command_include_line(beta_ids, file="module.py")

        result = run_git_command(["show", ":module.py"])
        assert result.stdout == "".join(rewritten)

    def test_explicit_file_include_line_refreshes_the_selected_file_view(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line should advance the selected file view."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 81)]
        lines[0] = "from old_alpha import thing\n"
        lines[10] = "from old_beta import helper\n"
        lines[60] = "body_call_old()\n"
        file_path = repo / "module.py"
        file_path.write_text("".join(lines))
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        rewritten = (
            ["from new_alpha import (\n", "    thing,\n", "    helper,\n", ")\n"]
            + lines[1:10]
            + ["from new_beta import helper\n"]
            + lines[11:60]
            + ["body_call_new()\n"]
            + lines[61:]
        )
        file_path.write_text("".join(rewritten))

        command_start()
        command_show(file="module.py")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None

        def ids_for_texts(*texts: str) -> str:
            selected_ids = [
                str(line.id)
                for line in line_changes.lines
                if line.id is not None and line.display_text() in texts
            ]
            assert len(selected_ids) == len(texts)
            return ",".join(selected_ids)

        alpha_ids = ids_for_texts(
            "from old_alpha import thing",
            "from new_alpha import (",
            "    thing,",
            "    helper,",
            ")",
        )
        assert ids_for_texts(
            "from old_beta import helper",
            "from new_beta import helper",
        )
        body_ids = ids_for_texts("body_call_old()", "body_call_new()")

        command_include_line(body_ids, file="module.py")
        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        remaining_texts = [line.display_text() for line in line_changes.lines if line.id is not None]
        assert "body_call_old()" not in remaining_texts
        assert "body_call_new()" not in remaining_texts
        assert "from old_beta import helper" in remaining_texts

        command_include_line(alpha_ids, file="module.py")
        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        remaining_ids = [line.id for line in line_changes.lines if line.id is not None]
        remaining_texts = [line.display_text() for line in line_changes.lines if line.id is not None]
        assert remaining_ids == [1, 2]
        assert remaining_texts == [
            "from old_beta import helper",
            "from new_beta import helper",
        ]

        command_include_line("1,2", file="module.py")

        result = run_git_command(["show", ":module.py"])
        assert result.stdout == "".join(rewritten)

    def test_show_file_displays_gap_markers_between_real_hunks(self, tmp_path, monkeypatch, capsys):
        """File-scoped displays should show omitted unchanged regions explicitly."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 121)]
        (repo / "multi.txt").write_text("".join(lines))
        subprocess.run(["git", "add", "multi.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        content = lines[:]
        content.insert(5, "change-one\n")
        content.insert(55, "change-two\n")
        content.insert(105, "change-three\n")
        (repo / "multi.txt").write_text("".join(content))

        command_start()
        capsys.readouterr()
        command_show(file="multi.txt")

        captured = capsys.readouterr()
        assert captured.out.count("... 43 more lines ...") == 2

    def test_show_file_discard_line_keeps_file_selection_for_multi_hunk_file(self, tmp_path, monkeypatch):
        """Discard --line from a file selection should keep the remaining file view."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 121)]
        (repo / "multi.txt").write_text("".join(lines))
        subprocess.run(["git", "add", "multi.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        content = lines[:]
        content.insert(5, "change-one\n")
        content.insert(55, "change-two\n")
        content.insert(105, "change-three\n")
        (repo / "multi.txt").write_text("".join(content))

        command_start()
        command_show(file="multi.txt")

        command_discard_line("1")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        changed_texts = [line.display_text() for line in line_changes.lines if line.kind != " "]
        assert "change-one" not in changed_texts
        assert "change-two" in changed_texts
        assert "change-three" in changed_texts

    def test_explicit_file_discard_line_uses_the_refreshed_file_view(self, tmp_path, monkeypatch):
        """Explicit file-scoped discard --line should follow the refreshed file view."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 81)]
        lines[0] = "from old_alpha import thing\n"
        lines[10] = "from old_beta import helper\n"
        lines[60] = "body_call_old()\n"
        file_path = repo / "module.py"
        file_path.write_text("".join(lines))
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        rewritten = lines[:]
        rewritten[0] = "from new_alpha import thing\n"
        rewritten[10] = "from new_beta import helper\n"
        rewritten[60] = "body_call_new()\n"
        file_path.write_text("".join(rewritten))

        command_start()
        command_show(file="module.py")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None

        def ids_for_texts(*texts: str) -> str:
            selected_ids = [
                str(line.id)
                for line in line_changes.lines
                if line.id is not None and line.display_text() in texts
            ]
            assert len(selected_ids) == len(texts)
            return ",".join(selected_ids)

        alpha_ids = ids_for_texts(
            "from old_alpha import thing",
            "from new_alpha import thing",
        )
        body_ids = ids_for_texts("body_call_old()", "body_call_new()")

        command_include_line(alpha_ids, file="module.py")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        remaining_texts = [line.display_text() for line in line_changes.lines if line.id is not None]
        assert "from old_beta import helper" in remaining_texts
        assert "from new_beta import helper" in remaining_texts
        assert "body_call_old()" in remaining_texts
        assert "body_call_new()" in remaining_texts

        body_ids = ",".join(
            str(line.id)
            for line in line_changes.lines
            if line.id is not None and line.display_text() in {
                "body_call_old()",
                "body_call_new()",
            }
        )
        command_discard_line(body_ids, file="module.py")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        remaining_texts = [line.display_text() for line in line_changes.lines if line.id is not None]
        assert "body_call_old()" not in remaining_texts
        assert "body_call_new()" not in remaining_texts

        beta_ids = ",".join(
            str(line.id)
            for line in line_changes.lines
            if line.id is not None and line.display_text() in {
                "from old_beta import helper",
                "from new_beta import helper",
            }
        )
        command_discard_line(beta_ids, file="module.py")

        assert file_path.read_text() == (
            "from new_alpha import thing\n"
            + "".join(lines[1:10])
            + "from old_beta import helper\n"
            + "".join(lines[11:60])
            + "body_call_old()\n"
            + "".join(lines[61:])
        )

    def test_explicit_file_include_to_batch_line_uses_the_refreshed_file_view(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --to --line should follow the refreshed file view."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 81)]
        lines[0] = "from old_alpha import thing\n"
        lines[10] = "from old_beta import helper\n"
        lines[60] = "body_call_old()\n"
        file_path = repo / "module.py"
        file_path.write_text("".join(lines))
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        rewritten = lines[:]
        rewritten[0] = "from new_alpha import thing\n"
        rewritten[10] = "from new_beta import helper\n"
        rewritten[60] = "body_call_new()\n"
        file_path.write_text("".join(rewritten))

        command_start()
        command_show(file="module.py")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None

        alpha_ids = ",".join(
            str(line.id)
            for line in line_changes.lines
            if line.id is not None and line.display_text() in {
                "from old_alpha import thing",
                "from new_alpha import thing",
            }
        )
        command_include_line(alpha_ids, file="module.py")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        body_ids = ",".join(
            str(line.id)
            for line in line_changes.lines
            if line.id is not None and line.display_text() in {
                "body_call_old()",
                "body_call_new()",
            }
        )

        command_include_to_batch("body-only", line_ids=body_ids, file="module.py")

        metadata = read_batch_metadata("body-only")
        file_metadata = metadata["files"]["module.py"]
        assert file_metadata["presence_claims"][0]["source_lines"] == ["61"]
        assert file_metadata["deletions"][0]["after_source_line"] == 60

        command_show_from_batch("body-only")
        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        non_context_texts = [line.display_text() for line in line_changes.lines if line.id is not None]
        assert "body_call_old()" in non_context_texts
        assert "body_call_new()" in non_context_texts
        assert "from old_beta import helper" not in non_context_texts
        assert "from new_beta import helper" not in non_context_texts

    def test_show_file_skip_line_keeps_file_selection_for_multi_hunk_file(self, tmp_path, monkeypatch):
        """Skip --line from a file selection should keep the remaining file view."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 121)]
        (repo / "multi.txt").write_text("".join(lines))
        subprocess.run(["git", "add", "multi.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        content = lines[:]
        content.insert(5, "change-one\n")
        content.insert(55, "change-two\n")
        content.insert(105, "change-three\n")
        (repo / "multi.txt").write_text("".join(content))

        command_start()
        command_show(file="multi.txt")

        command_skip_line("1")

        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        visible_ids = [line.id for line in line_changes.lines if line.id is not None]
        changed_texts = [line.display_text() for line in line_changes.lines if line.id is not None]
        assert 1 not in visible_ids
        assert "change-one" not in changed_texts
        assert "change-two" in changed_texts
        assert "change-three" in changed_texts

    def test_show_files_outputs_navigation_list_and_clears_selection(self, multi_file_repo, capsys):
        """Show --files should be navigational, not a hidden selected-file action."""
        command_start()
        capsys.readouterr()

        args = parse_command_line(["show", "--files", "*.txt"], quiet=True)
        assert args is not None
        args.func(args)

        captured = capsys.readouterr()
        assert "── matched files" in captured.out
        assert "Changes: file vs HEAD" in captured.out
        assert "Open:" in captured.out
        assert "git-stage-batch show --file alpha.txt" in captured.out
        assert read_selected_change_kind() is None
        with pytest.raises(CommandError, match="last command only showed files"):
            command_include(quiet=True)
        result = run_git_command(["diff", "--cached", "--name-only"])
        assert result.stdout == ""

    def test_include_files_reports_aggregate_staged_scope(self, multi_file_repo, capsys):
        """Include --files should report the full staged scope once."""
        command_start()
        capsys.readouterr()

        args = parse_command_line(["include", "--files", "*.txt"], quiet=True)
        assert args is not None
        args.func(args)

        captured = capsys.readouterr()
        assert "✓ Staged 3 hunks from 3 files" in captured.err
        assert "alpha.txt" not in captured.err
        assert "beta.txt" not in captured.err
        assert "gamma.txt" not in captured.err

        result = run_git_command(["diff", "--cached", "--name-only"])
        assert result.stdout.splitlines() == ["alpha.txt", "beta.txt", "gamma.txt"]

    def test_discard_to_batch_files_reports_aggregate_scope_once(self, multi_file_repo, capsys):
        """Discard --to --files should not render the next selected hunk per file."""
        command_start()
        capsys.readouterr()

        args = parse_command_line(["discard", "--to", "saved", "--files", "*.txt"], quiet=True)
        assert args is not None
        args.func(args)

        captured = capsys.readouterr()
        assert "✓ Saved 3 hunks from 3 files to batch 'saved' and discarded them" in captured.err
        assert captured.out.count("alpha.txt") == 0
        assert captured.out.count("beta.txt") == 0
        assert captured.out.count("gamma.txt") == 0

        result = run_git_command(["diff", "--name-only"])
        assert result.stdout == ""
        metadata = read_batch_metadata("saved")
        assert sorted(metadata["files"]) == ["alpha.txt", "beta.txt", "gamma.txt"]

    def test_include_files_can_restage_manually_unstaged_file_in_same_session(self, multi_file_repo, capsys):
        """Manual unstaging should not leave file-scoped include blocked in-session."""
        command_start()
        capsys.readouterr()

        args = parse_command_line(["include", "--files", "*.txt"], quiet=True)
        assert args is not None
        args.func(args)
        capsys.readouterr()

        subprocess.run(
            ["git", "restore", "--staged", "gamma.txt"],
            check=True,
            cwd=multi_file_repo,
            capture_output=True,
        )

        args = parse_command_line(["show", "--files", "gamma.txt"], quiet=True)
        assert args is not None
        args.func(args)
        captured = capsys.readouterr()
        assert "gamma2-modified" in captured.out
        assert "gamma4-new" in captured.out

        args = parse_command_line(["include", "--files", "gamma.txt"], quiet=True)
        assert args is not None
        args.func(args)

        captured = capsys.readouterr()
        assert "✓ Staged 1 hunk from gamma.txt" in captured.err

        result = run_git_command(["diff", "--cached", "--name-only"])
        assert result.stdout.splitlines() == ["alpha.txt", "beta.txt", "gamma.txt"]

    def test_show_files_does_not_feed_bare_discard(self, multi_file_repo, capsys):
        """A multi-file file list should not leave a selected file for discard."""
        command_start()
        capsys.readouterr()

        args = parse_command_line(["show", "--files", "*.txt"], quiet=True)
        assert args is not None
        args.func(args)
        capsys.readouterr()

        assert read_selected_change_kind() is None
        with pytest.raises(CommandError, match="last command only showed files"):
            command_discard(quiet=True)
        assert (multi_file_repo / "alpha.txt").read_text() == "alpha1\nalpha2-modified\nalpha3\nalpha4-new\n"
        assert (multi_file_repo / "beta.txt").read_text() == "beta1\nbeta2-modified\nbeta3\nbeta4-new\n"
        assert (multi_file_repo / "gamma.txt").read_text() == "gamma1\ngamma2-modified\ngamma3\ngamma4-new\n"

    def test_show_files_does_not_feed_bare_skip(self, multi_file_repo, capsys):
        """A multi-file file list should not leave a selected file for skip."""
        command_start()
        capsys.readouterr()

        args = parse_command_line(["show", "--files", "*.txt"], quiet=True)
        assert args is not None
        args.func(args)
        capsys.readouterr()

        assert read_selected_change_kind() is None
        with pytest.raises(CommandError, match="last command only showed files"):
            command_skip(quiet=True)


class TestIncludeToBatchWithFile:
    """Test include --to BATCH with --file flag."""

    def test_include_file_to_batch_saves_entire_file(self, multi_file_repo):
        """Include --to BATCH --file PATH should save all changes from that file."""
        command_start()
        command_include_to_batch("mybatch", file="beta.txt")

        # Verify batch contains beta.txt
        metadata = read_batch_metadata("mybatch")
        assert "beta.txt" in metadata.get("files", {})

        # Verify batch contains only the requested file.
        assert "alpha.txt" not in metadata.get("files", {})
        assert "gamma.txt" not in metadata.get("files", {})

    def test_include_file_to_batch_shows_all_changes(self, multi_file_repo, capsys):
        """Batch should contain all changes from the file."""
        command_start()
        capsys.readouterr()  # Clear start's output
        command_include_to_batch("batch", file="gamma.txt")

        command_show_from_batch("batch")

        captured = capsys.readouterr()
        # Should show both the modified line and new line
        assert "gamma2-modified" in captured.out or "gamma2" in captured.out
        assert "gamma4-new" in captured.out or "gamma4" in captured.out

    def test_include_file_empty_string_uses_selected_file(self, multi_file_repo):
        """Include --to BATCH --file (empty) should use selected hunk's file."""
        command_start()
        # Current hunk is alpha.txt
        command_include_to_batch("batch", file="")

        metadata = read_batch_metadata("batch")
        assert "alpha.txt" in metadata.get("files", {})

    def test_include_file_with_line_ids(self, multi_file_repo, capsys):
        """Include --to BATCH --file PATH --line IDS should save only specified lines."""
        command_start()
        # Save only line 2 from beta.txt
        command_include_to_batch("partial", line_ids="2", file="beta.txt")


        command_show_from_batch("partial")
        line_changes = load_line_changes_from_state()

        # Should have only 1 non-context line (renumbered to ID 1 in batch display)
        non_context_ids = [line.id for line in line_changes.lines
                          if line.id is not None and line.kind != " "]
        assert non_context_ids == [1]

        # Verify it's the correct content (beta2-modified from line ID 2)
        non_context_lines = [line for line in line_changes.lines if line.id is not None and line.kind != " "]
        assert len(non_context_lines) == 1
        assert "beta2-modified" in non_context_lines[0].display_text()


class TestDiscardToBatchWithFile:
    """Test discard --to BATCH with --file flag."""

    def test_discard_file_to_batch_saves_and_reverts(self, multi_file_repo):
        """Discard --to BATCH --file PATH should save changes and revert file."""
        command_start()
        command_discard_to_batch("saved", file="beta.txt")

        # File should be reverted to HEAD
        content = (multi_file_repo / "beta.txt").read_text()
        assert content == "beta1\nbeta2\nbeta3\n"

        # Other files should be unchanged
        content = (multi_file_repo / "alpha.txt").read_text()
        assert "alpha2-modified" in content

    def test_discard_file_to_batch_without_path_advances_selected_file(self, multi_file_repo, capsys):
        """Pathless --file should use the next selected file after a file route."""
        command_start()
        capsys.readouterr()

        command_discard_to_batch("first", file="")
        captured = capsys.readouterr()
        assert "Discarded file 'alpha.txt' to batch 'first'" in captured.err
        assert "beta.txt" in captured.out

        command_discard_to_batch("second", file="")
        captured = capsys.readouterr()
        assert "Discarded file 'beta.txt' to batch 'second'" in captured.err
        assert "No changes in file 'alpha.txt'" not in captured.err

        assert (multi_file_repo / "alpha.txt").read_text() == "alpha1\nalpha2\nalpha3\n"
        assert (multi_file_repo / "beta.txt").read_text() == "beta1\nbeta2\nbeta3\n"
        assert "gamma2-modified" in (multi_file_repo / "gamma.txt").read_text()

    def test_discard_file_to_batch_without_path_uses_displayed_patch_path(self, multi_file_repo, capsys):
        """Pathless --file should route the displayed patch if derived line state lags."""

        command_start()
        capsys.readouterr()

        beta_diff = subprocess.run(
            ["git", "diff", "-U3", "--no-color", "HEAD", "--", "beta.txt"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        get_selected_hunk_patch_file_path().write_text(beta_diff)
        get_selected_hunk_hash_file_path().write_text(
            compute_stable_hunk_hash_from_lines(
                beta_diff.encode("utf-8").splitlines(keepends=True)
            )
        )

        command_discard_to_batch("displayed", file="")

        assert "alpha2-modified" in (multi_file_repo / "alpha.txt").read_text()
        assert (multi_file_repo / "beta.txt").read_text() == "beta1\nbeta2\nbeta3\n"

    def test_discard_file_to_batch_preserves_changes_in_batch(self, multi_file_repo, capsys):
        """Discarded changes should be preserved in batch."""
        command_start()
        capsys.readouterr()  # Clear start's output
        command_discard_to_batch("preserve", file="gamma.txt")

        command_show_from_batch("preserve")

        captured = capsys.readouterr()
        assert "gamma2-modified" in captured.out or "gamma2" in captured.out
        assert "gamma4-new" in captured.out or "gamma4" in captured.out

    def test_discard_file_with_line_ids_partial_revert(self, multi_file_repo):
        """Discard --to BATCH --file PATH --line IDS should remove only those lines."""
        command_start()
        # Discard line IDs 1,2 (deletion + modification) from alpha.txt
        command_discard_to_batch("partial", line_ids="1,2", file="alpha.txt")

        content = (multi_file_repo / "alpha.txt").read_text()
        # Both deletion and addition should be discarded, leaving original line
        assert "alpha2\n" in content
        assert "alpha2-modified" not in content
        # Line 4 should still be present
        assert "alpha4-new" in content


class TestBatchSourceWithFile:
    """Test batch source operations (--from) with --file flag."""

    def test_include_from_batch_with_file_specific(self, multi_file_repo):
        """Include --from BATCH --file PATH should stage only that file."""
        command_start()
        # Save multiple files to batch
        command_include_to_batch("multi", file="alpha.txt")
        command_include_to_batch("multi", file="beta.txt")
        command_include_to_batch("multi", file="gamma.txt")

        # Revert all
        subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)

        # Include only beta.txt
        command_include_from_batch("multi", file="beta.txt")

        # Only beta.txt should be staged
        result = run_git_command(["diff", "--cached", "--name-only"])
        staged = result.stdout.strip().split("\n")
        assert "beta.txt" in staged
        assert "alpha.txt" not in staged
        assert "gamma.txt" not in staged

    def test_include_from_batch_file_empty_string_uses_selected_batch_file(self, multi_file_repo):
        """Include --from BATCH --file (empty) should use a selected batch file."""
        command_start()
        command_include_to_batch("batch", file="alpha.txt")
        command_include_to_batch("batch", file="beta.txt")

        subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)

        # Show one batch file to cache an explicit selected file.
        command_show_from_batch("batch", file="alpha.txt")

        # Include with empty string
        command_include_from_batch("batch", file="")

        # First file (alpha.txt) should be staged
        result = run_git_command(["diff", "--cached", "--name-only"])
        assert "alpha.txt" in result.stdout

    def test_include_from_batch_file_empty_string_after_file_list_fails(self, multi_file_repo):
        """A multi-file batch file list should not select a hidden batch file."""
        command_start()
        command_include_to_batch("batch", file="alpha.txt")
        command_include_to_batch("batch", file="beta.txt")

        subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)

        command_show_from_batch("batch")

        with pytest.raises(CommandError, match="last command only showed files"):
            command_include_from_batch("batch", file="")

    def test_apply_from_batch_with_file(self, multi_file_repo):
        """Apply --from BATCH --file PATH should apply only that file."""
        command_start()
        # Save and revert files
        command_discard_to_batch("apply-batch", file="alpha.txt")
        command_discard_to_batch("apply-batch", file="beta.txt")

        # Files are reverted
        assert "alpha2\n" in (multi_file_repo / "alpha.txt").read_text()
        assert "beta2\n" in (multi_file_repo / "beta.txt").read_text()

        # Apply only alpha.txt
        command_apply_from_batch("apply-batch", file="alpha.txt")

        # Only alpha.txt should have changes
        assert "alpha2-modified" in (multi_file_repo / "alpha.txt").read_text()
        assert "beta2\n" in (multi_file_repo / "beta.txt").read_text()

    def test_discard_from_batch_with_file(self, multi_file_repo):
        """Discard --from BATCH --file PATH should revert only that file."""
        command_start()
        # Save files
        command_discard_to_batch("batch", file="alpha.txt")
        command_discard_to_batch("batch", file="beta.txt")

        # Apply them back
        command_apply_from_batch("batch")

        # Both have changes
        assert "alpha2-modified" in (multi_file_repo / "alpha.txt").read_text()
        assert "beta2-modified" in (multi_file_repo / "beta.txt").read_text()

        # Discard only beta.txt
        command_discard_from_batch("batch", file="beta.txt")

        # beta should be reverted, alpha should keep changes
        assert "beta2\n" in (multi_file_repo / "beta.txt").read_text()
        assert "alpha2-modified" in (multi_file_repo / "alpha.txt").read_text()

    def test_batch_source_file_with_line_ids(self, multi_file_repo):
        """Batch source --file PATH --line IDS should operate on specific lines."""
        # Create a simpler file for this test
        (multi_file_repo / "simple.txt").write_text("line1\nline2\n")
        subprocess.run(["git", "add", "simple.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add simple"], check=True, capture_output=True)
        (multi_file_repo / "simple.txt").write_text("line1\nline2\nline3\nline4\n")

        command_start()
        command_discard_to_batch("batch", file="simple.txt")

        # Batch display shows additions: [#1] line1, [#2] line2, [#3] line3, [#4] line4
        # Apply only line 3 (not line 4)
        command_apply_from_batch("batch", line_ids="3", file="simple.txt")

        content = (multi_file_repo / "simple.txt").read_text()
        # Should have line3
        assert "line3\n" in content
        # line4 should remain out of the staged diff.
        assert "line4" not in content
        # Should still have original lines
        assert "line1\n" in content
        assert "line2\n" in content


class TestMultiFileBatchDisplay:
    """Test displaying multi-file batches with separators."""

    def test_show_from_multi_file_batch_list(self, multi_file_repo, capsys):
        """Show --from BATCH with multiple files should show a navigational file list."""
        command_start()
        capsys.readouterr()  # Clear start's output
        command_include_to_batch("multi", file="alpha.txt")
        command_include_to_batch("multi", file="beta.txt")
        command_include_to_batch("multi", file="gamma.txt")

        command_show_from_batch("multi")

        captured = capsys.readouterr()
        assert "── matched files" in captured.out
        assert "Changes: batch multi" in captured.out
        assert "git-stage-batch show --from multi --file alpha.txt" in captured.out
        assert read_selected_change_kind() is None

    def test_show_from_multi_file_list_summarizes_all_files(self, multi_file_repo, capsys):
        """Show --from BATCH should summarize every matched file."""
        command_start()
        capsys.readouterr()  # Clear start's output
        command_include_to_batch("all", file="alpha.txt")
        command_include_to_batch("all", file="beta.txt")
        command_include_to_batch("all", file="gamma.txt")

        command_show_from_batch("all")

        captured = capsys.readouterr()
        assert "alpha.txt" in captured.out
        assert "beta.txt" in captured.out
        assert "gamma.txt" in captured.out

    def test_show_from_multi_file_open_command_uses_page_review(self, multi_file_repo, capsys):
        """Matched-file open commands should route to the page-aware batch review."""
        command_start()
        capsys.readouterr()
        command_include_to_batch("multi", file="alpha.txt")
        command_include_to_batch("multi", file="beta.txt")

        command_show_from_batch("multi")
        capsys.readouterr()

        command_show_from_batch("multi", file="alpha.txt")

        captured = capsys.readouterr()
        assert "alpha.txt  ·  multi  ·  page 1/1" in captured.out

    def test_batch_files_maintain_insertion_order(self, multi_file_repo):
        """Files in batch should maintain insertion order, not alphabetical."""
        command_start()
        # Insert in non-alphabetical order
        command_include_to_batch("ordered", file="gamma.txt")
        command_include_to_batch("ordered", file="alpha.txt")
        command_include_to_batch("ordered", file="beta.txt")

        metadata = read_batch_metadata("ordered")
        files = list(metadata.get("files", {}).keys())

        # Should maintain insertion order
        assert files == ["gamma.txt", "alpha.txt", "beta.txt"]


class TestErrorHandling:
    """Test error handling for --file operations."""

    def test_file_not_in_batch_error(self, multi_file_repo):
        """Operating on file not in batch should fail with clear error."""
        command_start()
        command_include_to_batch("partial", file="alpha.txt")

        subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)

        with pytest.raises(CommandError, match="not found in batch"):
            command_include_from_batch("partial", file="beta.txt")

    def test_file_empty_string_without_cached_hunk_error(self, multi_file_repo):
        """Using --file (empty) without selected hunk should fail."""
        command_start()
        command_include_to_batch("batch", file="alpha.txt")

        # Remove the cached hunk state
        get_line_changes_json_file_path().unlink(missing_ok=True)
        get_selected_hunk_patch_file_path().unlink(missing_ok=True)
        get_selected_hunk_hash_file_path().unlink(missing_ok=True)

        # No selected hunk cached
        with pytest.raises(CommandError, match="No selected hunk"):
            command_include_from_batch("batch", file="")


class TestDirectFileOperations:
    """Test direct file operations (without --to batch)."""

    def test_discard_file_with_multiple_hunks(self, tmp_path, monkeypatch):
        """Discard --file should discard all hunks from a multi-hunk file."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        # Initialize git
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        # Create a file with well-separated changes to create multiple distinct hunks
        lines = []
        for i in range(1, 101):
            lines.append(f"line{i}\n")
        (repo / "test.txt").write_text("".join(lines))
        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        # Make additions at lines 10, 40, and 80 (well separated to create distinct hunks with -U3)
        content = (repo / "test.txt").read_text().splitlines(keepends=True)
        content.insert(10, "ADDED LINE 1\n")  # After line 10
        content.insert(40, "ADDED LINE 2\n")  # After line 40 (now 41 with first insertion)
        content.insert(80, "ADDED LINE 3\n")  # After line 80 (now 82 with previous insertions)
        (repo / "test.txt").write_text("".join(content))

        # Verify we have multiple hunks when using -U3 (as git-stage-batch does)
        result = subprocess.run(
            ["git", "diff", "-U3", "test.txt"],
            check=True,
            capture_output=True,
            text=True
        )
        hunk_count = result.stdout.count("\n@@")  # Count hunk headers
        assert hunk_count >= 2, f"Expected multiple hunks, got {hunk_count}"

        command_start()
        # Current hunk is from test.txt (the only file)

        # Discard entire file
        command_discard_file(file="")

        # File should be removed from working tree (git rm -f)
        assert not (repo / "test.txt").exists(), "File should be removed from working tree"

        # Verify it's staged for deletion
        result = run_git_command(["diff", "--cached", "--name-status"])
        assert "D\ttest.txt" in result.stdout, "File should be staged for deletion"

    def test_include_file_with_multiple_hunks(self, tmp_path, monkeypatch):
        """Include --file should stage all hunks from a multi-hunk file."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        # Initialize git
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        # Create a file with well-separated changes to create multiple distinct hunks
        lines = []
        for i in range(1, 101):
            lines.append(f"a{i}\n")
        (repo / "test.txt").write_text("".join(lines))
        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        # Make additions at lines 10, 40, and 80 (well separated to create distinct hunks with -U3)
        content = (repo / "test.txt").read_text().splitlines(keepends=True)
        content.insert(10, "ADDED A1\n")  # After line 10
        content.insert(40, "ADDED A2\n")  # After line 40
        content.insert(80, "ADDED A3\n")  # After line 80
        (repo / "test.txt").write_text("".join(content))

        # Verify we have multiple hunks when using -U3 (as git-stage-batch does)
        result = subprocess.run(
            ["git", "diff", "-U3", "test.txt"],
            check=True,
            capture_output=True,
            text=True
        )
        hunk_count = result.stdout.count("\n@@")  # Count hunk headers
        assert hunk_count >= 2, f"Expected multiple hunks, got {hunk_count}"

        command_start()
        # Current hunk is from test.txt (the only file)

        # Stage entire file
        command_include_file(file="")

        # Verify file is fully staged with all additions
        result = run_git_command(["diff", "--cached", "test.txt"])
        staged_diff = result.stdout
        assert "ADDED A1" in staged_diff
        assert "ADDED A2" in staged_diff
        assert "ADDED A3" in staged_diff

        # Verify working tree is clean (all changes staged)
        result = run_git_command(["diff", "test.txt"])
        assert result.stdout.strip() == ""


class TestFileAndLineIDCombinations:
    """Test combining --file with --line for fine-grained control."""

    def test_include_specific_lines_from_specific_file(self, multi_file_repo):
        """Should be able to save specific lines from a specific file."""
        command_start()
        # Save line 2 and 3 from beta.txt (beta2-modified and beta4-new)
        command_include_to_batch("precise", line_ids="2,3", file="beta.txt")


        command_show_from_batch("precise")
        line_changes = load_line_changes_from_state()

        non_context = [line for line in line_changes.lines if line.kind != " "]
        # Should have exactly 2 non-context lines (renumbered to 1,2 in batch display)
        assert len([line for line in non_context if line.id is not None]) == 2
        # Verify content
        assert any("beta2-modified" in line.display_text() for line in non_context)
        assert any("beta4-new" in line.display_text() for line in non_context)

    def test_discard_specific_lines_from_specific_file(self, multi_file_repo):
        """Should be able to discard specific lines from a specific file."""
        command_start()
        # Discard lines 1,2 from beta.txt (deletion + modification), keep line 3 (beta4-new)
        command_discard_to_batch("selective", line_ids="1,2", file="beta.txt")

        content = (multi_file_repo / "beta.txt").read_text()
        # Deletion and modification reverted
        assert "beta2\n" in content
        assert "beta2-modified" not in content
        # Line 3 (beta4-new) still present
        assert "beta4-new" in content

        # Other files untouched
        assert "alpha2-modified" in (multi_file_repo / "alpha.txt").read_text()

    def test_apply_specific_lines_from_specific_file_in_batch(self, multi_file_repo):
        """Should be able to apply specific lines from a specific file in multi-file batch."""
        command_start()
        command_discard_to_batch("multi", file="alpha.txt")
        command_discard_to_batch("multi", file="beta.txt")

        # Beta batch has: [#1] beta1, [#2] beta2 (deletion), [#3] beta2-modified, [#4] beta3, [#5] beta4-new
        # Deletions are shown in batch display (as suppression constraints)
        # Apply only line 5 from beta.txt (beta4-new)
        command_apply_from_batch("multi", line_ids="5", file="beta.txt")

        alpha_content = (multi_file_repo / "alpha.txt").read_text()
        beta_content = (multi_file_repo / "beta.txt").read_text()

        # Alpha should still be reverted (no changes applied)
        assert "alpha2\n" in alpha_content
        assert "alpha2-modified" not in alpha_content
        # Beta should have original beta2 and the new line beta4-new
        assert "beta2\n" in beta_content
        assert "beta4-new" in beta_content
        assert "beta2-modified" not in beta_content


class TestExplicitFilePath:
    """Test include --file PATH and discard --file PATH with explicit paths."""

    def test_include_file_with_explicit_path(self, multi_file_repo):
        """Include --file PATH should stage all hunks from specified file."""

        command_start()

        # Verify selected hunk is from alpha.txt (alphabetically first)
        line_changes_before = load_line_changes_from_state()
        assert line_changes_before.path == "alpha.txt", "Current hunk should be from alpha.txt"

        # Explicitly specify beta.txt (not the selected hunk's file)
        command_include_file(file="beta.txt")

        # Verify selected hunk is STILL from alpha.txt (unchanged)
        line_changes_after = load_line_changes_from_state()
        assert line_changes_after.path == "alpha.txt", "Current hunk should still be from alpha.txt"
        assert line_changes_after.lines == line_changes_before.lines, "Current hunk should be unchanged"

        # Verify beta.txt is fully staged
        result = run_git_command(["diff", "--cached", "beta.txt"])
        staged_diff = result.stdout
        assert "beta2-modified" in staged_diff
        assert "beta4-new" in staged_diff

        # Verify beta.txt working tree is clean (all changes staged)
        result = run_git_command(["diff", "beta.txt"])
        assert result.stdout.strip() == ""

        # Verify alpha.txt is still untouched (not staged)
        result = run_git_command(["diff", "--cached", "alpha.txt"])
        assert result.stdout.strip() == "", "alpha.txt should remain unstaged"
        result = run_git_command(["diff", "alpha.txt"])
        assert "alpha2-modified" in result.stdout, "alpha.txt should still have unstaged changes"

        # Verify gamma.txt is untouched
        result = run_git_command(["diff", "gamma.txt"])
        assert "gamma2-modified" in result.stdout

    def test_include_file_line_as_with_explicit_path(self, multi_file_repo):
        """Include --file PATH --line IDS --as should preserve selected position."""
        command_start()

        line_changes_before = load_line_changes_from_state()
        assert line_changes_before.path == "alpha.txt"

        command_include_line_as("2", "beta2-edited", file="beta.txt")

        line_changes_after = load_line_changes_from_state()
        assert line_changes_after.path == "alpha.txt"
        assert line_changes_after.lines == line_changes_before.lines

        staged_content = run_git_command(["show", ":beta.txt"]).stdout
        assert staged_content == "beta1\nbeta2-edited\nbeta3\n"

        working_diff = run_git_command(["diff", "beta.txt"]).stdout
        assert "beta4-new" in working_diff
        assert "beta2-modified" in working_diff
        assert "beta2-edited" in working_diff

    def test_include_line_with_explicit_path_does_not_reuse_other_file_ids(self, multi_file_repo):
        """Explicit file-scoped include --line should not inherit IDs from another file view."""
        command_start()
        command_show(file="alpha.txt")

        command_include_line("3", file="alpha.txt")
        command_include_line("1,2", file="beta.txt")

        line_changes_after = load_line_changes_from_state()
        assert line_changes_after.path == "alpha.txt"

        staged_content = run_git_command(["show", ":beta.txt"]).stdout
        assert staged_content == "beta1\nbeta2-modified\nbeta3\n"

        working_diff = run_git_command(["diff", "beta.txt"]).stdout
        assert "beta4-new" in working_diff
        assert "beta2-modified" in working_diff

    def test_include_line_with_explicit_path_preserves_gap_context_between_regions(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line should keep unchanged lines between added regions."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        base_lines = [
            'from dataclasses import dataclass\n',
            'from importlib import resources\n',
            'from pathlib import Path\n',
            'from typing import Protocol\n',
            'import sys\n',
            '\n',
            '@dataclass(frozen=True)\n',
            'class AssetGroup:\n',
            '    source_segments: tuple[str, ...]\n',
            '    target_segments: tuple[str, ...]\n',
            '    display_name_singular: str\n',
            '    display_name_plural: str\n',
            '    required_entry: str\n',
            '\n',
        ]
        base_lines.extend(f"filler{i}\n" for i in range(1, 33))
        base_lines.extend([
            'ASSET_GROUPS: dict[str, AssetGroup] = {\n',
            '    "claude-skills": AssetGroup(\n',
            '        source_segments=("assets", "claude-skills"),\n',
            '        target_segments=(".claude", "skills"),\n',
            '        display_name_singular="Claude skill",\n',
            '        display_name_plural="Claude skills",\n',
            '        required_entry="SKILL.md",\n',
            '    ),\n',
            '}\n',
        ])
        file_path = repo / "assets_module.py"
        file_path.write_text("".join(base_lines))
        subprocess.run(["git", "add", "assets_module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        rewritten = base_lines[:]
        rewritten.insert(3, "import subprocess\n")
        rewritten.insert(8, "from .patterns import resolve_gitignore_style_patterns\n")

        helper_insert_at = 12
        helper_lines = [
            '\n',
            '@dataclass(frozen=True)\n',
            'class CompanionAsset:\n',
            '    source_segments: tuple[str, ...]\n',
            '    target_segments: tuple[str, ...]\n',
            '    display_name: str\n',
        ]
        for offset, line in enumerate(helper_lines):
            rewritten.insert(helper_insert_at + offset, line)

        asset_insert_at = len(rewritten) - 1
        codex_lines = [
            '    "codex-skills": AssetGroup(\n',
            '        source_segments=("assets", "codex-skills"),\n',
            '        target_segments=(".agents", "skills"),\n',
            '        display_name_singular="Codex skill",\n',
            '        display_name_plural="Codex skills",\n',
            '        required_entry="SKILL.md",\n',
            '        companion_assets=(\n',
            '            CompanionAsset(\n',
            '                source_segments=("assets", "codex-skills", "config", "config.toml"),\n',
            '                target_segments=(".codex", "config.toml"),\n',
            '                display_name="Codex config",\n',
            '            ),\n',
            '        ),\n',
            '    ),\n',
        ]
        for offset, line in enumerate(codex_lines):
            rewritten.insert(asset_insert_at + offset, line)

        file_path.write_text("".join(rewritten))

        command_start()

        def ids_for_texts(*texts: str) -> str:
            line_changes = render_unstaged_file_as_single_hunk("assets_module.py")
            assert line_changes is not None
            selected_ids = [
                str(line.id)
                for line in line_changes.lines
                if line.id is not None and line.display_text() in texts
            ]
            assert len(selected_ids) == len(texts)
            return ",".join(selected_ids)

        helper_ids = ids_for_texts(
            "import subprocess",
            "from .patterns import resolve_gitignore_style_patterns",
            "@dataclass(frozen=True)",
            "class CompanionAsset:",
            "    source_segments: tuple[str, ...]",
            "    target_segments: tuple[str, ...]",
            "    display_name: str",
        )
        command_include_line(helper_ids, file="assets_module.py")

        remaining_line_changes = render_unstaged_file_as_single_hunk("assets_module.py")
        assert remaining_line_changes is not None
        codex_ids = ",".join(
            str(line.id)
            for line in remaining_line_changes.lines
            if line.id is not None
        )
        command_include_line(codex_ids, file="assets_module.py")

        staged_content = run_git_command(["show", ":assets_module.py"]).stdout
        assert "@dataclass(frozen=True)" in staged_content
        assert 'class CompanionAsset:' in staged_content
        assert (
            '        required_entry="SKILL.md",\n'
            '    ),\n'
            '    "codex-skills": AssetGroup(\n'
        ) in staged_content

    def test_include_line_as_with_explicit_path_preserves_gap_context_between_regions(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line --as should replace the selected region only."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        lines = [f"line{i}\n" for i in range(1, 41)]
        file_path = repo / "multi.txt"
        file_path.write_text("".join(lines))
        subprocess.run(["git", "add", "multi.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        rewritten = lines[:]
        rewritten[5] = "first working\n"
        rewritten[20] = "middle working\n"
        rewritten[35] = "last working\n"
        file_path.write_text("".join(rewritten))

        command_start()

        line_changes = render_unstaged_file_as_single_hunk("multi.txt")
        assert line_changes is not None
        middle_ids = ",".join(
            str(line.id)
            for line in line_changes.lines
            if line.id is not None and line.display_text() in {"line21", "middle working"}
        )
        assert middle_ids == "3,4"

        command_include_line_as(middle_ids, "middle staged", file="multi.txt")

        staged_content = run_git_command(["show", ":multi.txt"]).stdout
        expected = lines[:]
        expected[20] = "middle staged\n"
        assert staged_content == "".join(expected)

        working_diff = run_git_command(["diff", "multi.txt"]).stdout
        assert "-line6" in working_diff
        assert "+first working" in working_diff
        assert "-middle staged" in working_diff
        assert "+middle working" in working_diff
        assert "-line36" in working_diff
        assert "+last working" in working_diff

    def test_include_line_as_with_explicit_path_trims_matching_edge_anchors(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line --as should accept unchanged edge anchors."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        file_path = repo / "module.py"
        file_path.write_text("keep1\nkeep2\nold\nkeep4\n")
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        file_path.write_text("keep1\nkeep2\nnew\nkeep4\n")

        command_start()
        command_include_line_as("1-2", "keep1\nkeep2\nstaged\nkeep4\n", file="module.py")

        staged_content = run_git_command(["show", ":module.py"]).stdout
        assert staged_content == "keep1\nkeep2\nstaged\nkeep4\n"

    def test_include_line_as_with_explicit_path_no_edge_overlap_keeps_edge_anchors(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line --as --no-edge-overlap should preserve duplicate anchors."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        file_path = repo / "module.py"
        file_path.write_text("keep1\nkeep2\nold\nkeep4\n")
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        file_path.write_text("keep1\nkeep2\nnew\nkeep4\n")

        command_start()
        command_include_line_as(
            "1-2",
            "keep1\nkeep2\nstaged\nkeep4\n",
            file="module.py",
            no_edge_overlap=True,
        )

        staged_content = run_git_command(["show", ":module.py"]).stdout
        assert staged_content == "keep1\nkeep2\nkeep1\nkeep2\nstaged\nkeep4\nkeep4\n"

    def test_include_file_as_with_explicit_path_stages_full_file_text(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --as should stage the full replacement file text."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        file_path = repo / "module.py"
        file_path.write_text("keep1\nkeep2\nold\nkeep4\n")
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        file_path.write_text("keep1\nkeep2\nnew\nkeep4\n")

        command_start()
        command_include_file_as("keep1\nkeep2\nstaged\nkeep4\n", file="module.py")

        staged_content = run_git_command(["show", ":module.py"]).stdout
        assert staged_content == "keep1\nkeep2\nstaged\nkeep4\n"
        assert file_path.read_text() == "keep1\nkeep2\nnew\nkeep4\n"

    def test_include_line_with_explicit_path_rejects_partial_replacement_range(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line should refuse a partial replacement range."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        file_path = repo / "module.py"
        file_path.write_text("keep\nold value\n")
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        file_path.write_text("keep\nnew value 1\nnew value 2\nnew value 3\n")

        command_start()

        with pytest.raises(CommandError) as exc_info:
            command_include_line("1-2", file="module.py")

        assert (
            str(exc_info.value)
            == "Contiguous line selections cannot split one replacement. "
            "Select --lines 1-4 instead, pick individual "
            "lines one at a time, or use --as."
        )

        result = run_git_command(["diff", "--cached", "--name-only"])
        assert result.stdout == ""

    def test_include_line_with_explicit_path_rejects_partial_later_structural_run(self, tmp_path, monkeypatch):
        """Explicit file-scoped include --line should reject a partial later run."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        file_path = repo / "module.py"
        file_path.write_text(
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "def omega():\n"
            "    return 2\n"
        )
        subprocess.run(["git", "add", "module.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        file_path.write_text(
            "def alpha():\n"
            "    value = 1\n"
            "    return value\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "def omega():\n"
            "    return 2\n"
            "\n"
            "def helper():\n"
            "    if True:\n"
            "        return 3\n"
        )

        command_start()

        line_changes = render_unstaged_file_as_single_hunk("module.py")
        assert line_changes is not None
        helper_def_id = next(
            line.id
            for line in line_changes.lines
            if line.id is not None and line.display_text() == "def helper():"
        )
        first_changed_id = min(line_changes.changed_line_ids())
        selection = f"{first_changed_id}-{helper_def_id}"

        with pytest.raises(CommandError) as exc_info:
            command_include_line(selection, file="module.py")

        assert (
            str(exc_info.value)
            == "That line range crosses separate changes while selecting only part of one. "
            "Select one change at a time, include every line in the range, or use --as."
        )

        result = run_git_command(["diff", "--cached", "--name-only"])
        assert result.stdout == ""

    def test_discard_file_with_explicit_path(self, multi_file_repo):
        """Discard --file PATH should discard all hunks from specified file."""

        command_start()

        # Verify selected hunk is from alpha.txt (alphabetically first)
        line_changes_before = load_line_changes_from_state()
        assert line_changes_before.path == "alpha.txt", "Current hunk should be from alpha.txt"

        # Explicitly specify gamma.txt (not the selected hunk's file)
        command_discard_file(file="gamma.txt")

        # Verify selected hunk is STILL from alpha.txt (unchanged)
        line_changes_after = load_line_changes_from_state()
        assert line_changes_after.path == "alpha.txt", "Current hunk should still be from alpha.txt"
        assert line_changes_after.lines == line_changes_before.lines, "Current hunk should be unchanged"

        # Verify gamma.txt is removed
        assert not (multi_file_repo / "gamma.txt").exists()

        # Verify it's staged for deletion
        result = run_git_command(["diff", "--cached", "--name-status"])
        assert "D\tgamma.txt" in result.stdout

        # Verify alpha.txt is still untouched
        alpha_content = (multi_file_repo / "alpha.txt").read_text()
        assert "alpha2-modified" in alpha_content

        # Verify beta.txt is untouched
        beta_content = (multi_file_repo / "beta.txt").read_text()
        assert "beta2-modified" in beta_content

    def test_discard_file_line_as_to_batch_with_explicit_path(self, multi_file_repo):
        """Discard --to BATCH --file PATH --line --as should preserve selected position."""
        command_start()

        line_changes_before = load_line_changes_from_state()
        assert line_changes_before.path == "alpha.txt"

        from git_stage_batch.commands.discard import command_discard_line_as_to_batch

        command_discard_line_as_to_batch(
            "feature-batch",
            "2",
            "beta2-edited",
            file="beta.txt",
        )

        line_changes_after = load_line_changes_from_state()
        assert line_changes_after.path == "alpha.txt"
        assert line_changes_after.lines == line_changes_before.lines

        beta_content = (multi_file_repo / "beta.txt").read_text()
        assert beta_content == "beta1\nbeta2\nbeta3\nbeta4-new\n"

        command_apply_from_batch("feature-batch")

        applied_content = (multi_file_repo / "beta.txt").read_text()
        assert applied_content == "beta1\nbeta2-edited\nbeta3\nbeta4-new\n"

    def test_discard_file_as_with_explicit_path_preserves_selected_position(self, multi_file_repo):
        """Discard --file PATH --as should rewrite the working tree and preserve selection."""
        command_start()

        line_changes_before = load_line_changes_from_state()
        assert line_changes_before.path == "alpha.txt"

        command_discard_file_as("beta1\nbeta2-rewritten\nbeta3\n", file="beta.txt")

        line_changes_after = load_line_changes_from_state()
        assert line_changes_after.path == "alpha.txt"
        assert line_changes_after.lines == line_changes_before.lines

        beta_content = (multi_file_repo / "beta.txt").read_text()
        assert beta_content == "beta1\nbeta2-rewritten\nbeta3\n"

        result = run_git_command(["diff", "--cached", "--name-only"])
        assert result.stdout.strip() == ""

    def test_include_file_explicit_path_multiple_hunks(self, tmp_path, monkeypatch):
        """Include --file PATH should stage all hunks from multi-hunk file."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        # Initialize git
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        # Create multiple files
        (repo / "file1.txt").write_text("".join([f"a{i}\n" for i in range(1, 101)]))
        (repo / "file2.txt").write_text("".join([f"b{i}\n" for i in range(1, 101)]))
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        # Modify both files with multiple separated hunks
        content1 = (repo / "file1.txt").read_text().splitlines(keepends=True)
        content1.insert(10, "FILE1-CHANGE1\n")
        content1.insert(50, "FILE1-CHANGE2\n")
        content1.insert(90, "FILE1-CHANGE3\n")
        (repo / "file1.txt").write_text("".join(content1))

        content2 = (repo / "file2.txt").read_text().splitlines(keepends=True)
        content2.insert(20, "FILE2-CHANGE1\n")
        content2.insert(60, "FILE2-CHANGE2\n")
        (repo / "file2.txt").write_text("".join(content2))

        command_start()
        # Current hunk is from file1.txt (alphabetically first)

        # Explicitly stage file2.txt (not the selected file)
        command_include_file(file="file2.txt")

        # Verify file2.txt is fully staged
        result = run_git_command(["diff", "--cached", "file2.txt"])
        staged_diff = result.stdout
        assert "FILE2-CHANGE1" in staged_diff
        assert "FILE2-CHANGE2" in staged_diff

        # Verify file1.txt remains unstaged.
        result = run_git_command(["diff", "--cached", "file1.txt"])
        assert result.stdout.strip() == ""

        # Verify file1.txt still has unstaged changes
        result = run_git_command(["diff", "file1.txt"])
        assert "FILE1-CHANGE1" in result.stdout

    def test_discard_file_explicit_path_multiple_hunks(self, tmp_path, monkeypatch):
        """Discard --file PATH should discard all hunks from multi-hunk file."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        # Initialize git
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)

        # Create multiple files
        (repo / "keep.txt").write_text("".join([f"k{i}\n" for i in range(1, 101)]))
        (repo / "remove.txt").write_text("".join([f"r{i}\n" for i in range(1, 101)]))
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

        # Modify both files
        content_keep = (repo / "keep.txt").read_text().splitlines(keepends=True)
        content_keep.insert(10, "KEEP1\n")
        content_keep.insert(50, "KEEP2\n")
        (repo / "keep.txt").write_text("".join(content_keep))

        content_remove = (repo / "remove.txt").read_text().splitlines(keepends=True)
        content_remove.insert(20, "REMOVE1\n")
        content_remove.insert(70, "REMOVE2\n")
        (repo / "remove.txt").write_text("".join(content_remove))

        command_start()
        # Current hunk is from keep.txt (alphabetically first)

        # Explicitly discard remove.txt (not the selected file)
        command_discard_file(file="remove.txt")

        # Verify remove.txt is removed
        assert not (repo / "remove.txt").exists()

        # Verify it's staged for deletion
        result = run_git_command(["diff", "--cached", "--name-status"])
        assert "D\tremove.txt" in result.stdout

        # Verify keep.txt is untouched
        content = (repo / "keep.txt").read_text()
        assert "KEEP1" in content
        assert "KEEP2" in content

    def test_include_file_masks_hunks_from_iteration(self, multi_file_repo):
        """After include --file PATH, that file's hunks should be masked from iteration."""

        command_start()

        # Current hunk is from alpha.txt (alphabetically first)
        line_changes = load_line_changes_from_state()
        assert line_changes.path == "alpha.txt"

        # Explicitly include beta.txt (not the selected file)
        command_include_file(file="beta.txt")

        # Beta.txt should be fully staged and its working tree should be clean
        result = run_git_command(["diff", "beta.txt"])
        assert result.stdout.strip() == "", "beta.txt should have no unstaged changes"

        # Now iterate through remaining hunks - beta.txt should be masked
        # We should only see alpha.txt and gamma.txt hunks
        seen_files = []
        for _ in range(10):  # Iterate up to 10 times (we have 3 files total)
            line_changes = load_line_changes_from_state()
            if line_changes is None:
                break
            if line_changes.path not in seen_files:
                seen_files.append(line_changes.path)
            # Skip selected hunk to advance
            command_skip(quiet=True)

        # Should see alpha.txt and gamma.txt while beta.txt is masked.
        assert "alpha.txt" in seen_files, "Should see alpha.txt hunks"
        assert "gamma.txt" in seen_files, "Should see gamma.txt hunks"
        assert "beta.txt" not in seen_files, "beta.txt hunks should be masked"

    def test_discard_file_masks_hunks_from_iteration(self, multi_file_repo):
        """After discard --file PATH, that file's hunks should be masked from iteration."""

        command_start()

        # Current hunk is from alpha.txt (alphabetically first)
        line_changes = load_line_changes_from_state()
        assert line_changes.path == "alpha.txt"

        # Explicitly discard gamma.txt (not the selected file)
        command_discard_file(file="gamma.txt")

        # Gamma.txt should be removed
        assert not (multi_file_repo / "gamma.txt").exists(), "gamma.txt should be removed"

        # Now iterate through remaining hunks - gamma.txt should be masked
        # We should only see alpha.txt and beta.txt hunks
        seen_files = []
        for _ in range(10):  # Iterate up to 10 times
            line_changes = load_line_changes_from_state()
            if line_changes is None:
                break
            if line_changes.path not in seen_files:
                seen_files.append(line_changes.path)
            # Skip selected hunk to advance
            command_skip(quiet=True)

        # Should see alpha.txt and beta.txt while gamma.txt is masked.
        assert "alpha.txt" in seen_files, "Should see alpha.txt hunks"
        assert "beta.txt" in seen_files, "Should see beta.txt hunks"
        assert "gamma.txt" not in seen_files, "gamma.txt hunks should be masked"
