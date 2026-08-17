"""Tests for include from batch command."""

import os
import stat
import subprocess
import sys

import pytest

import git_stage_batch.commands.batch_source.include_action as include_action
from git_stage_batch.cli.argument_parser import parse_command_line
from git_stage_batch.batch.state.lifecycle import create_batch
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.state.query import read_batch_metadata
from git_stage_batch.batch.text_file_storage import add_file_to_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.discard import command_discard_to_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.commands.redo import command_redo
from git_stage_batch.commands.show import command_show
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.stop import command_stop
from git_stage_batch.commands.undo import command_undo
from git_stage_batch.batch.file_display import render_batch_file_display
from git_stage_batch.data.file_review.state import clear_last_file_review_state
from git_stage_batch.data.line_state import load_line_changes_from_state
from git_stage_batch.data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
)
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.paths import ensure_state_directory_exists
from tests.buffer_helpers import uses_mapped_storage


_RUNNING_UNDER_XDIST = "PYTEST_XDIST_WORKER" in os.environ
_PROCESS_TEST = pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"} or _RUNNING_UNDER_XDIST,
    reason="forced process coverage runs on Linux and macOS with pytest -n 0",
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "add", "README.md"], check=True, cwd=repo, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    # Initialize session for batch operations
    ensure_state_directory_exists()
    initialize_abort_state()

    return repo


class TestCommandIncludeFromBatch:
    """Tests for include from batch command."""

    def test_include_from_batch_added_symlink_keeps_worktree_and_index_consistent(
        self,
        temp_git_repo,
    ):
        """Including an added symlink batch should write a symlink and stage one."""
        link_path = temp_git_repo / "link"
        os.symlink("target", link_path)
        command_start(quiet=True)
        command_include_to_batch("test-batch", line_ids="1", file="link", quiet=True)

        link_path.unlink()
        command_include_from_batch("test-batch")

        index_entry = run_git_command(["ls-files", "-s", "--", "link"]).stdout
        blob = run_git_command(["show", ":link"]).stdout
        assert os.path.islink(link_path)
        assert os.readlink(link_path) == "target"
        assert index_entry.split()[0] == "120000"
        assert blob.encode() == b"target"

    def test_include_from_batch_stages_changes(self, temp_git_repo, capsys):
        """Test including changes from a batch stages them."""

        # Create a new file and save to batch
        (temp_git_repo / "new.txt").write_text("new content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Remove from working tree and unstage
        (temp_git_repo / "new.txt").unlink()
        run_git_command(["reset", "HEAD", "new.txt"], check=False)

        command_include_from_batch("test-batch")

        # Verify file is staged
        result = run_git_command(["diff", "--cached", "--name-only"])
        assert "new.txt" in result.stdout
        assert (temp_git_repo / "new.txt").read_text() == "new content\n"

        captured = capsys.readouterr()
        assert "Staged changes from batch" in captured.err

    def test_include_from_merges_disjoint_batch_after_head_advances(
        self,
        temp_git_repo,
    ):
        """A batch should merge after an earlier disjoint batch is committed."""
        driver = temp_git_repo / "driver.c"
        baseline = """\
static struct state *duplicate_state(void)
{
        struct state *state;
        struct frame_info *frame_info;

        state = allocate_state();
        if (!state)
                return NULL;

        frame_info = allocate_frame_info();
        if (!frame_info) {
                free_state(state);
                return NULL;
        }

        state->frame_info = frame_info;
        return state;
}

static void keep_first_boundary_stable(void)
{
        keep_line_one();
        keep_line_two();
        keep_line_three();
        keep_line_four();
        keep_line_five();
        keep_line_six();
}

static void destroy_state(struct state *state)
{
        struct controller *controller = state->controller;

        if (controller && state->frame_info->buffer) {
                if (buffer_has_references(state->frame_info->buffer))
                        buffer_put(state->frame_info->buffer);
        }

        free_frame_info(state->frame_info);
        free_state(state);
}

static void keep_second_boundary_stable(void)
{
        keep_line_seven();
        keep_line_eight();
        keep_line_nine();
        keep_line_ten();
        keep_line_eleven();
        keep_line_twelve();
}

static void reset_state(void)
{
        struct state *state;

        state = allocate_state();
        if (!state)
                return;

        install_state(state);
}

static void keep_third_boundary_stable(void)
{
        keep_line_thirteen();
        keep_line_fourteen();
        keep_line_fifteen();
        keep_line_sixteen();
        keep_line_seventeen();
        keep_line_eighteen();
}

static struct plane *initialize_properties(struct device *device)
{
        struct plane *plane;

        plane = allocate_plane(device,
                               supported_formats,
                               supported_format_count,
                               primary_plane_type);
        if (!plane)
                return NULL;

        add_plane_helpers(plane);

        create_rotation_property(plane);
        create_color_properties(plane);

        return plane;
}
"""
        helper_version = """\
static struct state *allocate_complete_state(void)
{
        struct state *state;

        state = allocate_state();
        if (!state)
                return NULL;

        state->frame_info = allocate_frame_info();
        if (!state->frame_info) {
                free_state(state);
                return NULL;
        }

        return state;
}

static struct state *duplicate_state(void)
{
        struct state *state;

        state = allocate_complete_state();
        if (!state)
                return NULL;

        return state;
}

static void keep_first_boundary_stable(void)
{
        keep_line_one();
        keep_line_two();
        keep_line_three();
        keep_line_four();
        keep_line_five();
        keep_line_six();
}

static void destroy_state(struct state *state)
{
        if (state->frame_info && state->frame_info->buffer)
                buffer_put(state->frame_info->buffer);

        free_frame_info(state->frame_info);
        free_state(state);
}

static void keep_second_boundary_stable(void)
{
        keep_line_seven();
        keep_line_eight();
        keep_line_nine();
        keep_line_ten();
        keep_line_eleven();
        keep_line_twelve();
}

static void reset_state(void)
{
        struct state *state;

        state = allocate_complete_state();
        if (!state)
                return;

        install_state(state);
}

static void keep_third_boundary_stable(void)
{
        keep_line_thirteen();
        keep_line_fourteen();
        keep_line_fifteen();
        keep_line_sixteen();
        keep_line_seventeen();
        keep_line_eighteen();
}

static struct plane *initialize_properties(struct device *device)
{
        struct plane *plane;

        plane = allocate_plane(device,
                               supported_formats,
                               supported_format_count,
                               primary_plane_type);
        if (!plane)
                return NULL;

        add_plane_helpers(plane);

        create_rotation_property(plane);
        create_color_properties(plane);

        return plane;
}
"""
        property_version = baseline.replace(
            "        struct plane *plane;\n",
            "        struct plane *plane;\n        int result;\n",
        ).replace(
            "        create_rotation_property(plane);\n"
            "        create_color_properties(plane);\n",
            "        result = create_rotation_property(plane);\n"
            "        if (result)\n"
            "                return NULL;\n\n"
            "        result = create_color_properties(plane);\n"
            "        if (result)\n"
            "                return NULL;\n",
        )
        final_version = helper_version.replace(
            "        struct plane *plane;\n",
            "        struct plane *plane;\n        int result;\n",
        ).replace(
            "        create_rotation_property(plane);\n"
            "        create_color_properties(plane);\n",
            "        result = create_rotation_property(plane);\n"
            "        if (result)\n"
            "                return NULL;\n\n"
            "        result = create_color_properties(plane);\n"
            "        if (result)\n"
            "                return NULL;\n",
        )
        driver.write_text(baseline)
        subprocess.run(
            ["git", "add", "driver.c"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add driver"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        driver.write_text(final_version)
        command_start(quiet=True)
        command_discard_to_batch("helper", quiet=True)
        command_discard_to_batch("helper", quiet=True)
        command_discard_to_batch("helper", quiet=True)
        assert driver.read_text() == property_version
        command_discard_to_batch("properties", file="driver.c", quiet=True)
        assert driver.read_text() == baseline
        command_stop()

        command_include_from_batch("helper", file="driver.c")
        subprocess.run(
            ["git", "commit", "-m", "Extract allocator"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        command_include_from_batch("properties", file="driver.c")

        assert driver.read_text() == final_version
        assert run_git_command(["show", ":driver.c"]).stdout == final_version

    def test_multi_file_write_failure_rolls_back_index_and_worktree(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A later worktree failure must roll back prior staging and writes."""
        for name in ("a.txt", "b.txt"):
            (temp_git_repo / name).write_text(f"{name} base\n")
        subprocess.run(
            ["git", "add", "a.txt", "b.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add files"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        for name in ("a.txt", "b.txt"):
            (temp_git_repo / name).write_text(f"{name} batch\n")

        command_start(quiet=True)
        command_include_to_batch("test-batch", file="a.txt", quiet=True)
        command_include_to_batch("test-batch", file="b.txt", quiet=True)
        for name in ("a.txt", "b.txt"):
            (temp_git_repo / name).write_text(f"{name} base\n")

        original_write = include_action._text_file_actions.write_text_file_to_worktree
        calls = 0

        def fail_second_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            return original_write(*args, **kwargs)

        monkeypatch.setattr(
            include_action._text_file_actions,
            "write_text_file_to_worktree",
            fail_second_write,
        )

        with pytest.raises(CommandError, match="injected write failure"):
            command_include_from_batch("test-batch")

        assert (temp_git_repo / "a.txt").read_text() == "a.txt base\n"
        assert (temp_git_repo / "b.txt").read_text() == "b.txt base\n"
        staged = run_git_command(["diff", "--cached", "--name-only"])
        assert staged.stdout == ""

    def test_plan_cleanup_cancellation_rolls_back_index_and_worktree(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Include plan teardown must finish before transaction commit."""
        path = temp_git_repo / "file.txt"
        path.write_text("base\n")
        subprocess.run(
            ["git", "add", "file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        path.write_text("batch\n")
        command_start(quiet=True)
        command_include_to_batch("cleanup", file="file.txt", quiet=True)
        path.write_text("base\n")

        real_close = include_action._action_plans.close_resources

        def close_then_cancel(resources):
            real_close(resources)
            raise KeyboardInterrupt("include cleanup cancelled")

        monkeypatch.setattr(
            include_action._action_plans,
            "close_resources",
            close_then_cancel,
        )
        capsys.readouterr()

        with pytest.raises(KeyboardInterrupt, match="include cleanup cancelled"):
            command_include_from_batch("cleanup", file="file.txt")

        assert path.read_text() == "base\n"
        assert run_git_command(["show", ":file.txt"]).stdout == "base\n"
        assert "✓ Staged" not in capsys.readouterr().err

    def test_cli_multi_file_no_session_failure_is_atomic(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """CLI --files must publish one transient multi-file transaction."""
        for name in ("a.txt", "b.txt"):
            (temp_git_repo / name).write_text(f"{name} base\n")
        subprocess.run(
            ["git", "add", "a.txt", "b.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add files"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        for name in ("a.txt", "b.txt"):
            (temp_git_repo / name).write_text(f"{name} batch\n")
        command_start(quiet=True)
        command_include_to_batch("test-batch", file="a.txt", quiet=True)
        command_include_to_batch("test-batch", file="b.txt", quiet=True)
        for name in ("a.txt", "b.txt"):
            (temp_git_repo / name).write_text(f"{name} base\n")
        command_stop()

        original_write = include_action._text_file_actions.write_text_file_to_worktree
        calls = 0

        def fail_second_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected CLI write failure")
            return original_write(*args, **kwargs)

        monkeypatch.setattr(
            include_action._text_file_actions,
            "write_text_file_to_worktree",
            fail_second_write,
        )
        monkeypatch.setattr(
            include_action,
            "snapshot_file_if_untracked",
            lambda _path: (_ for _ in ()).throw(
                AssertionError("outside-session include wrote abort state")
            ),
        )
        args = parse_command_line(
            ["include", "--from", "test-batch", "--files", "*.txt"],
            quiet=True,
        )
        assert args is not None

        with pytest.raises(CommandError, match="injected CLI write failure"):
            args.func(args)

        assert (temp_git_repo / "a.txt").read_text() == "a.txt base\n"
        assert (temp_git_repo / "b.txt").read_text() == "b.txt base\n"
        assert run_git_command(["diff", "--cached", "--name-only"]).stdout == ""
        assert (
            run_git_command(
                [
                    "for-each-ref",
                    "--format=%(refname)",
                    "refs/git-stage-batch/transactions",
                ]
            ).stdout
            == ""
        )

    def test_include_from_empty_batch_fails(self, temp_git_repo):
        """Test including from an empty batch fails."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

        with pytest.raises(CommandError):
            command_include_from_batch("empty-batch")

    def test_include_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test including from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_include_from_batch("nonexistent")

    def test_include_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test including from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_include_from_batch("test-batch")

    def test_include_from_batch_file_mode_filters_to_selected_file(
        self, temp_git_repo, capsys
    ):
        """Test that --file mode filters batch diff to selected file only."""

        # Create baseline with multiple files
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(
            ["git", "add", "file1.txt", "file2.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add files"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        # Add new lines to both files and save to batch
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\nfile1 added\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\nfile2 added\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Reset files to original
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")

        # Show file1 from the batch so --file without PATH reuses that selected file.
        command_show_from_batch("test-batch", file="file1.txt")
        capsys.readouterr()

        command_include_from_batch("test-batch", file="")

        # Verify only file1 is staged (not file2)
        result = run_git_command(["diff", "--cached", "--name-only"])
        staged_files = [f for f in result.stdout.strip().split("\n") if f]
        assert "file1.txt" in staged_files
        assert "file2.txt" not in staged_files

        # Verify file1 has the added line
        result = run_git_command(["show", ":file1.txt"])
        assert "file1 added" in result.stdout
        assert (
            temp_git_repo / "file1.txt"
        ).read_text() == "line 1\nline 2\nfile1 added\n"
        assert (temp_git_repo / "file2.txt").read_text() == "line A\nline B\n"

        captured = capsys.readouterr()
        assert "Staged changes for file1.txt from batch" in captured.err

    def test_include_from_batch_file_mode_requires_cached_hunk(self, temp_git_repo):
        """Test that --file mode requires a cached hunk."""

        # Create a new file and save to batch
        (temp_git_repo / "new.txt").write_text("content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Try to use --file without cached hunk (no fetch_next_change call after command_start)
        with pytest.raises(CommandError):
            command_include_from_batch("test-batch", file="")

    @pytest.mark.parametrize(
        "line_scoped",
        [False, True],
        ids=["whole-file", "line-scoped"],
    )
    def test_include_from_batch_preserves_later_same_file_changes(
        self,
        temp_git_repo,
        line_scoped,
    ):
        """Partial batch replay should merge already-landed source changes."""
        script_path = temp_git_repo / "smoke-test.sh"
        baseline = (
            "cleanup driver\n"
            "finish cleanup\n"
            "build driver\n"
            "verify driver\n"
            "verify legacy identity\n"
            "run topology test\n"
            "run cleanup test\n"
        )
        source = (
            "cleanup topology\n"
            "cleanup driver\n"
            "finish cleanup\n"
            "build driver and tests\n"
            "verify driver\n"
            "verify test module\n"
            "verify legacy identity\n"
            "run topology lifetime test\n"
            "run composition test\n"
            "run cleanup test\n"
        )
        current = (
            "cleanup topology\n"
            "cleanup driver\n"
            "finish cleanup\n"
            "build driver\n"
            "verify driver\n"
            "verify legacy identity\n"
            "run topology lifetime test\n"
            "run composition test\n"
            "run cleanup test\n"
        )
        expected = (
            "cleanup topology\n"
            "cleanup driver\n"
            "finish cleanup\n"
            "build driver and tests\n"
            "verify driver\n"
            "verify test module\n"
            "verify legacy identity\n"
            "run topology lifetime test\n"
            "run composition test\n"
            "run cleanup test\n"
        )

        script_path.write_text(baseline)
        run_git_command(["add", "smoke-test.sh"])
        run_git_command(["commit", "-m", "Add smoke test"])

        script_path.write_text(source)
        command_start(quiet=True)
        command_show(file="smoke-test.sh")
        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        owned_text = {
            "build driver",
            "build driver and tests",
            "verify test module",
        }
        owned_ids = ",".join(
            str(line.id)
            for line in line_changes.lines
            if line.id is not None and line.display_text() in owned_text
        )
        command_include_to_batch(
            "test-batch",
            line_ids=owned_ids,
            file="smoke-test.sh",
            quiet=True,
        )

        script_path.write_text(current)
        run_git_command(["add", "smoke-test.sh"])
        run_git_command(["commit", "-m", "Extend smoke coverage"])

        include_ids = None
        if line_scoped:
            command_show_from_batch("test-batch", file="smoke-test.sh")
            line_changes = load_line_changes_from_state()
            assert line_changes is not None
            include_ids = ",".join(
                str(line.id)
                for line in line_changes.lines
                if line.id is not None and line.display_text() in owned_text
            )
        command_include_from_batch(
            "test-batch",
            file="smoke-test.sh",
            line_ids=include_ids,
        )

        assert script_path.read_text() == expected
        assert run_git_command(["show", ":smoke-test.sh"]).stdout == expected

    def test_include_from_batch_restores_text_executable_mode(self, temp_git_repo):
        """Test whole-file include --from honors the batch target mode for text files."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(
            ["git", "add", "tool.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add tool"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        subprocess.run(
            ["git", "checkout", "HEAD", "--", "tool.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        command_include_from_batch("test-batch", file="tool.sh")

        index_entry = run_git_command(["ls-files", "-s", "--", "tool.sh"]).stdout
        assert index_entry.startswith("100755 ")
        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_apply_from_batch_restores_text_executable_mode(self, temp_git_repo):
        """Test whole-file apply --from honors the batch target mode for text files."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(
            ["git", "add", "tool.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add tool"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        subprocess.run(
            ["git", "checkout", "HEAD", "--", "tool.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        tool_path.chmod(0o644)

        command_apply_from_batch("test-batch", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_discard_from_batch_restores_text_baseline_executable_mode(
        self, temp_git_repo
    ):
        """Test whole-file discard --from honors the baseline mode for text files."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o755)
        subprocess.run(
            ["git", "add", "tool.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add executable tool"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        tool_path.write_text("#!/bin/sh\necho batched\n")
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o644)

        command_discard_from_batch("test-batch", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho base\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_discard_from_batch_removes_added_text_file(self, temp_git_repo):
        """Whole added text files saved to a batch discard back to absence."""
        new_path = temp_git_repo / "new.txt"
        new_path.write_text("new content\n")
        command_start()
        command_include_to_batch("test-batch", file="new.txt", quiet=True)

        file_meta = read_batch_metadata("test-batch")["files"]["new.txt"]
        assert file_meta["change_type"] == "added"

        command_discard_from_batch("test-batch", file="new.txt")

        assert not new_path.exists()

    def test_apply_from_batch_removes_deleted_text_file(self, temp_git_repo):
        """Whole deleted text files saved to a batch apply back to absence."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(
            ["git", "add", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add gone"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        file_meta = read_batch_metadata("test-batch")["files"]["gone.txt"]
        assert file_meta["change_type"] == "deleted"

        subprocess.run(
            ["git", "checkout", "HEAD", "--", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        command_apply_from_batch("test-batch", file="gone.txt")

        assert not gone_path.exists()
        assert (
            run_git_command(
                ["diff", "--cached", "--name-only", "--", "gone.txt"]
            ).stdout
            == ""
        )

    def test_apply_from_batch_line_scoped_deleted_text_file_removes_path(
        self, temp_git_repo
    ):
        """Selecting every deleted text line should preserve deleted-path semantics."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(
            ["git", "add", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add gone"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        subprocess.run(
            ["git", "checkout", "HEAD", "--", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        command_apply_from_batch("test-batch", line_ids="1", file="gone.txt")

        assert not gone_path.exists()
        assert (
            run_git_command(
                ["diff", "--cached", "--name-only", "--", "gone.txt"]
            ).stdout
            == ""
        )

    def test_apply_from_batch_line_scoped_added_text_file_restores_executable_mode(
        self, temp_git_repo
    ):
        """Line-scoped apply that creates a text path should use the batch mode."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        tool_path.unlink()
        command_apply_from_batch("test-batch", line_ids="1-2", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_include_from_batch_stages_deleted_text_file_and_removes_worktree_path(
        self, temp_git_repo
    ):
        """Whole deleted text files saved to a batch include as staged deletions."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(
            ["git", "add", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add gone"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        subprocess.run(
            ["git", "checkout", "HEAD", "--", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        command_include_from_batch("test-batch", file="gone.txt")

        assert not gone_path.exists()
        assert (
            run_git_command(["status", "--short", "--", "gone.txt"]).stdout
            == "D  gone.txt\n"
        )

    def test_include_from_batch_line_scoped_added_text_file_restores_executable_mode(
        self, temp_git_repo
    ):
        """Line-scoped include that creates a text path should use the batch mode."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        tool_path.unlink()
        run_git_command(["reset", "HEAD", "tool.sh"], check=False)
        command_include_from_batch("test-batch", line_ids="1-2", file="tool.sh")

        index_entry = run_git_command(["ls-files", "-s", "--", "tool.sh"]).stdout
        assert index_entry.startswith("100755 ")
        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_include_from_batch_accepts_complete_addition_boundary_without_cached_review(
        self,
        temp_git_repo,
    ):
        """A complete shared-boundary addition remains actionable without cache."""
        prefix = [
            "class X {\n",
            "    @Test\n",
            "    fun presentBefore() {\n",
            '        assert("before")\n',
            "    }\n",
            "\n",
        ]
        missing_middle = [
            "    @Test\n",
            "    fun missingOne() {\n",
            '        assert("body1")\n',
            "    }\n",
            "\n",
            "    @Test\n",
            "    fun missingTwo() {\n",
            '        assert("body2")\n',
            "    }\n",
            "\n",
        ]
        suffix = [
            "    @Test\n",
            "    fun next() {\n",
            '        assert("next")\n',
            "    }\n",
            *[f"    val filler{index} = {index}\n" for index in range(1, 40)],
            "}\n",
        ]
        test_file = temp_git_repo / "Test.kt"
        test_file.write_text("".join([*prefix, *missing_middle, *suffix]))
        command_start(quiet=True)
        command_include_to_batch("test-batch", file="Test.kt", quiet=True)

        test_file.write_text("".join([*prefix, *suffix]))
        subprocess.run(
            ["git", "add", "Test.kt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Partial"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        clear_last_file_review_state()

        command_include_from_batch("test-batch", line_ids="7-16", file="Test.kt")

        expected = "".join([*prefix, *missing_middle, *suffix])
        assert run_git_command(["show", ":Test.kt"]).stdout == expected
        assert test_file.read_text() == expected

    def test_include_from_batch_line_scoped_deleted_text_file_stages_deletion(
        self, temp_git_repo
    ):
        """Line-scoped include of a full deleted text file should stage path deletion."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(
            ["git", "add", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add gone"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        subprocess.run(
            ["git", "checkout", "HEAD", "--", "gone.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        command_include_from_batch("test-batch", line_ids="1", file="gone.txt")

        assert not gone_path.exists()
        assert (
            run_git_command(["status", "--short", "--", "gone.txt"]).stdout
            == "D  gone.txt\n"
        )

    def test_include_from_batch_creates_added_empty_text_file(self, temp_git_repo):
        """Empty added text files should not be skipped as empty ownership."""
        empty_path = temp_git_repo / "empty.txt"
        empty_path.write_text("")
        command_start()
        add_file_to_batch(
            "test-batch", "empty.txt", BatchOwnership.from_presence_lines([], [])
        )

        empty_path.unlink()
        command_include_from_batch("test-batch", file="empty.txt")

        assert empty_path.exists()
        assert empty_path.read_bytes() == b""
        assert (
            run_git_command(
                ["diff", "--cached", "--name-only", "--", "empty.txt"]
            ).stdout
            == "empty.txt\n"
        )

    def test_discard_from_batch_line_scoped_added_text_file_removes_path(
        self, temp_git_repo
    ):
        """Line-scoped discard of a full added text file should restore absence."""
        new_path = temp_git_repo / "new.txt"
        new_path.write_text("new\n")
        command_start()
        command_include_to_batch("test-batch", file="new.txt", quiet=True)

        command_discard_from_batch("test-batch", line_ids="1", file="new.txt")

        assert not new_path.exists()

    def test_discard_from_batch_partial_new_text_file_removes_path_when_exhausted(
        self, temp_git_repo
    ):
        """Discarding the remaining owned content of a partial new file should restore absence."""
        new_path = temp_git_repo / "new.txt"
        new_path.write_text("owned\nunowned\n")
        command_start()
        command_include_to_batch("test-batch", line_ids="1", file="new.txt", quiet=True)

        new_path.write_text("owned\n")

        command_discard_from_batch("test-batch", file="new.txt")

        assert not new_path.exists()

    def test_discard_from_batch_line_scoped_deleted_text_file_restores_executable_mode(
        self, temp_git_repo
    ):
        """Line-scoped discard that restores a deleted text path should use baseline mode."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o755)
        subprocess.run(
            ["git", "add", "tool.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add executable tool"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        tool_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        command_discard_from_batch("test-batch", line_ids="1-2", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho base\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_include_from_batch_as_replaces_presence_only_selection(
        self, temp_git_repo
    ):
        """Test include --from --line --as replaces claimed batch content before staging."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        test_file.write_text("keep\nbatch value\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("keep\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        batch_value_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.display_text() == "batch value"
        )

        command_include_from_batch(
            "test-batch",
            line_ids=str(batch_value_gutter),
            file="test.txt",
            replacement_text="edited value",
        )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "keep\nedited value\n"
        assert test_file.read_text() == "keep\nedited value\n"

    def test_include_from_batch_as_replaces_atomic_replacement_unit(
        self, temp_git_repo
    ):
        """Test include --from --line --as preserves batch deletion semantics."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep\nold value\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        test_file.write_text("keep\nbatch value\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("keep\nold value\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        replacement_gutters = sorted(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None
            and line.display_text() in {"old value", "batch value"}
        )

        command_include_from_batch(
            "test-batch",
            line_ids=f"{replacement_gutters[0]}-{replacement_gutters[-1]}",
            file="test.txt",
            replacement_text="edited value",
        )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "keep\nedited value\n"
        assert test_file.read_text() == "keep\nedited value\n"

    def test_include_from_batch_as_rejects_partial_replacement_unit(
        self, temp_git_repo
    ):
        """Test include --from --line --as honors explicit replacement atomicity."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("old one\nold two\nkeep\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        test_file.write_text("new one\nnew two\nkeep\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("old one\nold two\nkeep\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        new_one_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.display_text() == "new one"
        )

        with pytest.raises(CommandError, match="must be selected together"):
            command_include_from_batch(
                "test-batch",
                line_ids=str(new_one_gutter),
                file="test.txt",
                replacement_text="edited value",
            )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "old one\nold two\nkeep\n"
        assert test_file.read_text() == "old one\nold two\nkeep\n"

    def test_include_rejects_index_change_after_planning(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A changed index target should abort before include mutation."""
        target = temp_git_repo / "target.txt"
        target.write_text("base\n")
        run_git_command(["add", "target.txt"])
        run_git_command(["commit", "-m", "Add target"])
        target.write_text("batch\n")
        command_start(quiet=True)
        command_include_to_batch(
            "test-batch",
            file="target.txt",
            quiet=True,
        )
        target.write_text("worktree target\n")
        run_git_command(["reset", "HEAD", "target.txt"])

        real_run_file_jobs = include_action.run_file_jobs

        def mutate_index(*args, **kwargs):
            results = real_run_file_jobs(*args, **kwargs)
            object_id = run_git_command(
                ["hash-object", "-w", "--stdin"],
                stdin_chunks=(b"stale index\n",),
            ).stdout.strip()
            run_git_command(
                [
                    "update-index",
                    "--cacheinfo",
                    "100644",
                    object_id,
                    "target.txt",
                ]
            )
            return results

        monkeypatch.setattr(include_action, "run_file_jobs", mutate_index)

        with pytest.raises(CommandError, match="Index changed"):
            command_include_from_batch("test-batch")

        assert run_git_command(["show", ":target.txt"]).stdout == "stale index\n"
        assert target.read_text() == "worktree target\n"

    def test_include_rejects_worktree_change_after_planning(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A changed worktree target should abort before index mutation."""
        target = temp_git_repo / "target.txt"
        target.write_text("base\n")
        run_git_command(["add", "target.txt"])
        run_git_command(["commit", "-m", "Add target"])
        target.write_text("batch\n")
        command_start(quiet=True)
        command_include_to_batch(
            "test-batch",
            file="target.txt",
            quiet=True,
        )
        run_git_command(["checkout", "HEAD", "--", "target.txt"])

        real_run_file_jobs = include_action.run_file_jobs
        real_workspace = include_action.FileJobWorkspace
        workspace_roots = []

        def mutate_worktree(*args, **kwargs):
            results = real_run_file_jobs(*args, **kwargs)
            target.write_text("stale worktree\n")
            return results

        def record_workspace(*args, **kwargs):
            workspace = real_workspace(*args, **kwargs)
            workspace_roots.append(workspace.root)
            return workspace

        monkeypatch.setattr(include_action, "run_file_jobs", mutate_worktree)
        monkeypatch.setattr(
            include_action,
            "FileJobWorkspace",
            record_workspace,
        )

        with pytest.raises(CommandError, match="Working tree file changed"):
            command_include_from_batch("test-batch")

        assert run_git_command(["show", ":target.txt"]).stdout == "base\n"
        assert target.read_text() == "stale worktree\n"
        assert workspace_roots
        assert all(not root.exists() for root in workspace_roots)

    def test_include_reports_earlier_index_change_before_worktree_reads(
        self,
        monkeypatch,
    ):
        """Index staleness should win before any later worktree read failure."""
        expected_index_identities = {
            "first.txt": IndexIdentity("100644", "a" * 40),
            "second.txt": IndexIdentity("100644", "b" * 40),
        }
        expected_worktree_identities = {
            path: WorktreeIdentity(
                True,
                "regular",
                0o644,
                4,
                "digest",
            )
            for path in expected_index_identities
        }
        monkeypatch.setattr(
            include_action,
            "read_index_identities",
            lambda paths: {path: IndexIdentity(None, None) for path in paths},
        )
        monkeypatch.setattr(
            include_action,
            "capture_worktree_identity",
            lambda _path: (_ for _ in ()).throw(
                AssertionError("worktree read should not precede stale index")
            ),
        )

        with pytest.raises(CommandError, match="Index changed.*first.txt"):
            include_action._require_unchanged_include_targets(
                expected_index_identities,
                expected_worktree_identities,
            )

    @_PROCESS_TEST
    def test_forced_process_include_matches_inline_targets_and_undo(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Forced transports should publish the same ordered include result."""
        baseline = {}
        expected = {}
        for index in range(4):
            name = f"{index}.txt"
            baseline[name] = f"base-{index}-" + "x" * 5000 + "\n"
            expected[name] = f"batch-{index}-" + "y" * 5000 + "\n"
            (temp_git_repo / name).write_text(baseline[name])
        run_git_command(["add", "."])
        run_git_command(["commit", "-m", "Add process targets"])
        for name, content in expected.items():
            (temp_git_repo / name).write_text(content)
        command_start(quiet=True)
        for name in expected:
            command_include_to_batch(
                "test-batch",
                file=name,
                quiet=True,
            )
        capsys.readouterr()
        mapped_outputs = []
        original_stage = include_action._text_file_actions.stage_text_file_to_index
        original_write = include_action._text_file_actions.write_text_file_to_worktree

        def record_stage(file_path, buffer, file_mode, change_type):
            mapped_outputs.append(uses_mapped_storage(buffer))
            return original_stage(
                file_path,
                buffer,
                file_mode,
                change_type,
            )

        def record_write(file_path, buffer, file_mode, change_type):
            mapped_outputs.append(uses_mapped_storage(buffer))
            return original_write(
                file_path,
                buffer,
                file_mode,
                change_type,
            )

        monkeypatch.setattr(
            include_action._text_file_actions,
            "stage_text_file_to_index",
            record_stage,
        )
        monkeypatch.setattr(
            include_action._text_file_actions,
            "write_text_file_to_worktree",
            record_write,
        )

        observations = []
        for requested_jobs in ("1", "2"):
            run_git_command(["reset", "--hard", "HEAD"])
            monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", requested_jobs)

            command_include_from_batch("test-batch")

            observations.append(
                (
                    {name: (temp_git_repo / name).read_text() for name in expected},
                    run_git_command(["write-tree"]).stdout.strip(),
                    capsys.readouterr(),
                )
            )
            command_undo(force=True)
            assert {
                name: (temp_git_repo / name).read_text() for name in baseline
            } == baseline
            command_redo(force=True)
            assert {
                name: (temp_git_repo / name).read_text() for name in expected
            } == expected
            capsys.readouterr()

        assert observations[0] == observations[1]
        assert observations[0][0] == expected
        assert mapped_outputs == [True] * 16
