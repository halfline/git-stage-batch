"""Tests for discard from batch command."""

from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.stop import command_stop
from git_stage_batch.commands.include import command_include_to_batch

import subprocess

import pytest

import git_stage_batch.commands.batch_source.discard_action as discard_action
import git_stage_batch.data.undo.checkpoints as undo_checkpoints
from git_stage_batch.batch.state.lifecycle import create_batch
from git_stage_batch.batch.file_display import render_batch_file_display
from git_stage_batch.core.buffer import LineBuffer
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
from git_stage_batch.utils.file_job_workspace import FileJobWorkspace


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


class TestCommandDiscardFromBatch:
    """Tests for discard from batch command."""

    def test_discard_from_batch_removes_changes(self, temp_git_repo, capsys):
        """Test discarding changes from a batch removes them from working tree."""

        # Commit a file first
        (temp_git_repo / "file.txt").write_text("original\n")
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

    def test_cancelled_binary_plan_closes_acquired_buffer(
        self,
        monkeypatch,
    ):
        """Cancellation during binary metadata lookup must release content."""
        buffer = LineBuffer.from_bytes(b"binary\0content")
        monkeypatch.setattr(
            discard_action,
            "read_git_object_buffer_or_none",
            lambda *_args, **_kwargs: buffer,
        )

        def interrupt_mode_lookup(*_args, **_kwargs):
            raise KeyboardInterrupt("mode lookup cancelled")

        monkeypatch.setattr(
            discard_action,
            "detect_file_mode_in_commit",
            interrupt_mode_lookup,
        )

        with (
            FileJobWorkspace() as workspace,
            pytest.raises(KeyboardInterrupt, match="mode lookup cancelled"),
        ):
            discard_action._build_discard_binary_action_plan(
                ordinal=0,
                file_path="image.bin",
                baseline_commit="baseline",
                workspace=workspace,
            )

        with pytest.raises(ValueError, match="buffer is closed"):
            buffer.to_bytes()

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

    @pytest.mark.parametrize(
        ("failure_type", "expected_type"),
        (
            (OSError, CommandError),
            (KeyboardInterrupt, KeyboardInterrupt),
        ),
        ids=("write-error", "cancellation"),
    )
    @pytest.mark.parametrize(
        "active_session",
        (True, False),
        ids=("active-session", "outside-session"),
    )
    def test_explicit_multi_file_failure_rolls_back_earlier_discards(
        self,
        temp_git_repo,
        monkeypatch,
        failure_type,
        expected_type,
        active_session,
    ):
        """A later explicit --files failure must roll back earlier files."""
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
        if not active_session:
            command_stop()

        original_write = (
            discard_action._text_file_actions.write_discarded_text_file_to_worktree
        )
        calls = 0

        def fail_second_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise failure_type("injected write failure")
            return original_write(*args, **kwargs)

        monkeypatch.setattr(
            discard_action._text_file_actions,
            "write_discarded_text_file_to_worktree",
            fail_second_write,
        )

        with pytest.raises(expected_type, match="injected write failure"):
            command_discard_from_batch(
                "test-batch",
                file_paths=("a.txt", "b.txt"),
            )

        assert (temp_git_repo / "a.txt").read_text() == "a.txt batch\n"
        assert (temp_git_repo / "b.txt").read_text() == "b.txt batch\n"

    def test_plan_cleanup_cancellation_rolls_back_discard_without_session(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Discard plan teardown must finish before transient commit."""
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
        command_stop()

        real_close = discard_action._action_plans.close_resources

        def close_then_cancel(resources):
            real_close(resources)
            raise KeyboardInterrupt("discard cleanup cancelled")

        monkeypatch.setattr(
            discard_action._action_plans,
            "close_resources",
            close_then_cancel,
        )
        capsys.readouterr()

        with pytest.raises(KeyboardInterrupt, match="discard cleanup cancelled"):
            command_discard_from_batch("cleanup", file="file.txt")

        assert path.read_text() == "batch\n"
        assert "✓ Discarded" not in capsys.readouterr().err

    def test_discard_refusal_after_snapshot_preserves_concurrent_edit(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """The discard checkpoint must remain unarmed through its final check."""
        file_path = temp_git_repo / "file.txt"
        file_path.write_text("base\n")
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
        file_path.write_text("batch\n")
        command_start(quiet=True)
        command_include_to_batch("stale-batch", quiet=True)
        command_stop()

        real_create = undo_checkpoints._create_transient_transaction_checkpoint

        def mutate_after_snapshot(*args, **kwargs):
            checkpoint = real_create(*args, **kwargs)
            file_path.write_text("concurrent\n")
            return checkpoint

        monkeypatch.setattr(
            undo_checkpoints,
            "_create_transient_transaction_checkpoint",
            mutate_after_snapshot,
        )

        with pytest.raises(CommandError, match="Retry the discard command"):
            command_discard_from_batch("stale-batch")

        assert file_path.read_text() == "concurrent\n"
        transient_refs = subprocess.run(
            [
                "git",
                "for-each-ref",
                "--format=%(refname)",
                "refs/git-stage-batch/transactions/",
            ],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout
        assert transient_refs == ""

    def test_multi_file_planning_failure_precedes_every_write(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Every selected file must plan successfully before publication."""
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

        real_build = (
            discard_action._text_plan_builders.build_discard_text_file_action_plan
        )

        def fail_second_plan(*args, **kwargs):
            if kwargs["file_path"] == "b.txt":
                raise MergeError("injected planning failure")
            return real_build(*args, **kwargs)

        monkeypatch.setattr(
            discard_action._text_plan_builders,
            "build_discard_text_file_action_plan",
            fail_second_plan,
        )
        monkeypatch.setattr(
            discard_action._text_file_actions,
            "write_discarded_text_file_to_worktree",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("publication started before planning finished")
            ),
        )

        with pytest.raises(CommandError, match="a.txt|b.txt"):
            command_discard_from_batch("test-batch")

        assert (temp_git_repo / "a.txt").read_text() == "a.txt batch\n"
        assert (temp_git_repo / "b.txt").read_text() == "b.txt batch\n"

    def test_discard_rejects_stale_target_before_checkpoint(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A target changed after planning must not create an undo snapshot."""
        file_path = temp_git_repo / "file.txt"
        file_path.write_text("base\n")
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
        file_path.write_text("batch\n")
        command_start(quiet=True)
        command_include_to_batch("stale-batch", quiet=True)

        real_build = discard_action._build_discard_action_plans

        def mutate_after_planning(*args, **kwargs):
            capture = real_build(*args, **kwargs)
            file_path.write_text("concurrent\n")
            return capture

        monkeypatch.setattr(
            discard_action,
            "_build_discard_action_plans",
            mutate_after_planning,
        )
        monkeypatch.setattr(
            discard_action,
            "transaction_checkpoint",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("checkpoint started for stale target")
            ),
        )

        with pytest.raises(CommandError, match="Retry the discard command"):
            command_discard_from_batch("stale-batch")

        assert file_path.read_text() == "concurrent\n"

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
            command_discard_from_batch(
                "test-batch", line_ids=str(new_value_gutter), file="file.txt"
            )

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
