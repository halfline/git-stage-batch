"""Index snapshots tolerate transient contention without changing staged content."""

from contextlib import nullcontext
from pathlib import Path

import pytest

from git_stage_batch.data.undo import snapshots, state
from git_stage_batch.tui import session_startup
from git_stage_batch.utils import git_index, git_index_lock, session_start_point
from git_stage_batch.utils.git_command import run_git_command


@pytest.mark.parametrize(
    "operation",
    ["tree", "alternate-tree", "start-point", "interactive-start", "undo"],
)
def test_index_snapshot_waits_for_transient_lock(
    functional_repo, monkeypatch, operation
):
    """Each snapshot waits on its actual index and preserves the staged tree."""
    expected_tree = run_git_command(
        ["rev-parse", "HEAD^{tree}"], requires_index_lock=False
    ).stdout.strip()
    if operation == "interactive-start":
        (functional_repo / "README.md").write_text("Changed readme\n")
    legacy_state = snapshots.snapshot_current_state([])
    legacy_state.pop("index_entries")
    legacy_state["index_tree"] = expected_tree

    module = {
        "tree": git_index,
        "alternate-tree": git_index,
        "start-point": session_start_point,
        "interactive-start": session_startup,
        "undo": state,
    }[operation]
    index_context = (
        git_index.temp_git_index()
        if operation == "alternate-tree"
        else nullcontext(None)
    )
    with index_context as environment:
        if environment is not None:
            git_index.git_read_tree("HEAD", env=environment)
            index_path = Path(environment["GIT_INDEX_FILE"])
        else:
            index_path = functional_repo / ".git" / "index"
        lock_path = Path(f"{index_path}.lock")
        original = module.run_git_command
        waits = []
        injected = False

        def contend(arguments, *args, **kwargs):
            nonlocal injected
            if arguments == ["write-tree"] and not injected:
                injected = True
                lock_path.touch(exist_ok=False)
            return original(arguments, *args, **kwargs)

        def release_lock(seconds):
            # Release only when the waiter observes contention; no timing race.
            assert 0 < seconds <= git_index_lock.DEFAULT_INDEX_LOCK_POLL_SECONDS
            assert lock_path.exists()
            waits.append(seconds)
            lock_path.unlink()

        monkeypatch.setattr(module, "run_git_command", contend)
        monkeypatch.setattr(git_index_lock.time, "sleep", release_lock)
        try:
            if operation in {"tree", "alternate-tree"}:
                assert git_index.git_write_tree(env=environment) == expected_tree
            elif operation == "start-point":
                start_point = session_start_point.resolve_session_start_point()
                assert start_point.index_tree == expected_tree
            elif operation == "interactive-start":
                startup = session_startup.prepare_interactive_session()
                assert not startup.degraded_mode
            else:
                assert state._detect_conflicts_against_state(legacy_state) == []
            assert injected
            assert len(waits) == 1
            assert not lock_path.exists()
        finally:
            lock_path.unlink(missing_ok=True)

    assert run_git_command(
        ["write-tree"], requires_index_lock=True
    ).stdout.strip() == expected_tree
