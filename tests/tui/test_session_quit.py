"""Quit comparisons read staged content without waiting on index writers."""

import subprocess
from unittest.mock import Mock

import pytest

from git_stage_batch.tui import session_quit
from git_stage_batch.utils import git_index_lock
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.paths import (
    get_start_head_file_path,
    get_start_index_tree_file_path,
)


@pytest.fixture
def quit_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_git_command(["init"], requires_index_lock=False)
    run_git_command(["config", "user.name", "Test User"], requires_index_lock=False)
    run_git_command(
        ["config", "user.email", "test@example.com"], requires_index_lock=False
    )
    (tmp_path / "README.md").write_text("initial content\n")
    run_git_command(["add", "README.md"])
    run_git_command(["commit", "-m", "Initial commit"])
    head = run_git_command(
        ["rev-parse", "HEAD"], requires_index_lock=False
    ).stdout.strip()
    tree = run_git_command(["write-tree"]).stdout.strip()
    write_text_file_contents(get_start_head_file_path(), head)
    write_text_file_contents(get_start_index_tree_file_path(), tree)
    monkeypatch.setattr(session_quit, "get_hunk_counts", lambda: {})
    return tmp_path


@pytest.mark.parametrize(
    ("change", "should_prompt"),
    [
        ("none", False),
        ("unstaged", False),
        ("intent", False),
        ("staged", True),
        ("empty", True),
        ("mode", True),
        ("split", True),
        ("submodule", True),
        ("unmerged", True),
    ],
)
def test_quit_compares_index_while_lock_is_held(
    quit_repository, monkeypatch, change, should_prompt
):
    """A held lock neither delays quit nor hides staged changes."""
    path = quit_repository / "quit-test.txt"
    if change in {"unstaged", "intent", "staged", "empty", "mode", "split"}:
        path.write_text("" if change == "empty" else "staged content\n")
        if change == "intent":
            run_git_command(["add", "-N", path.name])
        elif change != "unstaged":
            run_git_command(["add", path.name])
        if change == "mode":
            tree = run_git_command(["write-tree"]).stdout.strip()
            write_text_file_contents(get_start_index_tree_file_path(), tree)
            run_git_command(["update-index", "--chmod=+x", path.name])
        elif change == "split":
            run_git_command(["update-index", "--split-index"])
    elif change == "submodule":
        head = run_git_command(
            ["rev-parse", "HEAD"], requires_index_lock=False
        ).stdout.strip()
        run_git_command(["update-index", "--add", "--cacheinfo", f"160000,{head},sub"])
        run_git_command(["config", "diff.ignoreSubmodules", "all"])
    elif change == "unmerged":
        blob = run_git_command(
            ["hash-object", "-w", "--stdin"], stdin_chunks=[b"conflict\n"]
        ).stdout.strip()
        run_git_command(
            ["update-index", "--index-info"],
            stdin_chunks=[f"100644 {blob} 1\tconflict\n100644 {blob} 2\tconflict\n".encode()],
        )

    prompt = Mock(return_value="cancel")
    stop = Mock()
    monkeypatch.setattr(session_quit, "prompt_quit_session", prompt)
    monkeypatch.setattr(session_quit, "command_stop", stop)
    monkeypatch.setattr(
        git_index_lock, "wait_for_git_index_lock",
        Mock(side_effect=AssertionError("Quit must not wait for index.lock")),
    )
    index = quit_repository / ".git" / "index"
    before = index.read_bytes()
    lock = index.with_name("index.lock")
    lock.write_text("another writer")
    try:
        session_quit.handle_quit()
        assert prompt.call_count == int(should_prompt)
        assert stop.call_count == int(not should_prompt)
        assert index.read_bytes() == before
        assert lock.read_text() == "another writer"
    finally:
        lock.unlink()


def test_quit_propagates_corrupt_index_error(quit_repository, monkeypatch):
    """Git errors must not become a successful quit or a changes prompt."""
    (quit_repository / ".git" / "index").write_bytes(b"invalid index")
    prompt = Mock()
    stop = Mock()
    monkeypatch.setattr(session_quit, "prompt_quit_session", prompt)
    monkeypatch.setattr(session_quit, "command_stop", stop)
    with pytest.raises(subprocess.CalledProcessError) as error:
        session_quit.handle_quit()
    assert error.value.returncode == 128
    assert error.value.stderr
    prompt.assert_not_called()
    stop.assert_not_called()
