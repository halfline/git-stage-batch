"""Tests for discard from batch command."""

from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.include import command_include_to_batch

import subprocess

import pytest

import git_stage_batch.commands.batch_source.discard_action as discard_action
from git_stage_batch.batch.state.lifecycle import create_batch
from git_stage_batch.batch.file_display import render_batch_file_display
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
)
from git_stage_batch.exceptions import CommandError, MergeError
from git_stage_batch.utils.paths import ensure_state_directory_exists


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

    # Initialize session for batch operations
    ensure_state_directory_exists()
    initialize_abort_state()

    return repo


class TestCommandDiscardFromBatch:
    """Tests for discard from batch command."""

    def test_discard_from_batch_removes_changes(self, temp_git_repo, capsys):
        """Test discarding changes from a batch removes them from working tree."""

        # Commit a file first
        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes and save to batch
        (temp_git_repo / "file.txt").write_text("batch version\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # File still has batch changes in working tree

        command_discard_from_batch("test-batch")

        # File should be back to committed state
        assert (temp_git_repo / "file.txt").read_text() == "original\n"

        captured = capsys.readouterr()
        assert "Discarded changes from batch" in captured.err

    @pytest.mark.parametrize("materialize", ["apply", "include"])
    def test_discard_from_reverses_applied_extracted_helper(
        self,
        temp_git_repo,
        materialize,
    ):
        """Discarding an applied helper extraction must restore the baseline."""
        plane = temp_git_repo / "plane.c"
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

static void destroy_state(struct state *state)
{
        struct controller *controller = state->controller;

        if (controller && state->frame_info->buffer) {
                /* Drop the reference acquired by primary_update(). */
                if (buffer_has_references(state->frame_info->buffer))
                        buffer_put(state->frame_info->buffer);
        }

        free_frame_info(state->frame_info);
        free_state(state);
}

static void reset_state(void)
{
        struct state *state;

        state = allocate_state();
        if (!state)
                return;

        install_state(state);
}
"""
        extracted = """\
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

static void destroy_state(struct state *state)
{
        if (state->frame_info && state->frame_info->buffer) {
                /* Drop the reference acquired by atomic_update(). */
                buffer_put(state->frame_info->buffer);
        }

        free_frame_info(state->frame_info);
        free_state(state);
}

static void reset_state(void)
{
        struct state *state;

        state = allocate_complete_state();
        if (!state)
                return;

        install_state(state);
}
"""
        plane.write_text(baseline)
        subprocess.run(
            ["git", "add", "plane.c"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add plane state"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        plane.write_text(extracted)
        command_start(quiet=True)
        command_include_to_batch("helper-extraction", file="plane.c", quiet=True)
        plane.write_text(baseline)

        if materialize == "apply":
            command_apply_from_batch("helper-extraction", file="plane.c")
        else:
            command_include_from_batch("helper-extraction", file="plane.c")
        assert plane.read_text() == extracted

        local_change = "/* unrelated local change */\n"
        plane.write_text(extracted + local_change)

        command_discard_from_batch("helper-extraction", file="plane.c")

        assert plane.read_text() == baseline + local_change

    def test_multi_file_failure_rolls_back_earlier_discards(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A later write failure must not leave earlier files discarded."""
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

        original_write = (
            discard_action._text_file_actions.write_discarded_text_file_to_worktree
        )
        calls = 0

        def fail_second_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            return original_write(*args, **kwargs)

        monkeypatch.setattr(
            discard_action._text_file_actions,
            "write_discarded_text_file_to_worktree",
            fail_second_write,
        )

        with pytest.raises(CommandError, match="a.txt|b.txt"):
            command_discard_from_batch("test-batch")

        assert (temp_git_repo / "a.txt").read_text() == "a.txt batch\n"
        assert (temp_git_repo / "b.txt").read_text() == "b.txt batch\n"

    def test_index_derived_rollback_rejects_change_after_planning(
        self,
        monkeypatch,
    ):
        """Discard must not use an index identity that went stale."""
        monkeypatch.setattr(
            discard_action,
            "read_index_identities",
            lambda _paths: {
                "file.txt": IndexIdentity("100644", "b" * 40),
            },
        )

        with pytest.raises(CommandError, match="Index changed.*file.txt"):
            discard_action._require_unchanged_discard_targets(
                {"file.txt": IndexIdentity("100644", "a" * 40)},
                {},
            )

    def test_discard_rejects_worktree_change_after_planning(
        self,
        monkeypatch,
    ):
        """Discard must not publish a plan built from stale worktree bytes."""
        monkeypatch.setattr(
            discard_action,
            "capture_worktree_identities",
            lambda _paths: {
                "file.txt": WorktreeIdentity(
                    True,
                    "regular",
                    0o644,
                    7,
                    "b" * 64,
                )
            },
        )

        with pytest.raises(CommandError, match="Working tree file changed"):
            discard_action._require_unchanged_discard_targets(
                {},
                {
                    "file.txt": WorktreeIdentity(
                        True,
                        "regular",
                        0o644,
                        7,
                        "a" * 64,
                    )
                },
            )

    def test_discard_from_batch_partial_atomic_unit_shows_required_lines(
        self, temp_git_repo
    ):
        """Partial replacement selections should keep the atomic-selection guidance."""
        test_file = temp_git_repo / "file.txt"
        test_file.write_text("old value\nkeep\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("new value\nkeep\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        rendered = render_batch_file_display("test-batch", "file.txt")
        new_value_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.display_text() == "new value"
        )

        with pytest.raises(CommandError, match="must be selected together") as exc_info:
            command_discard_from_batch("test-batch", line_ids=str(new_value_gutter), file="file.txt")

        assert "Use: --line" in exc_info.value.message

    def test_discard_from_empty_batch_fails(self, temp_git_repo):
        """Test discarding from an empty batch fails."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

        with pytest.raises(CommandError):
            command_discard_from_batch("empty-batch")

    def test_discard_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test discarding from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_discard_from_batch("nonexistent")

    def test_discard_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test discarding from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_discard_from_batch("test-batch")
