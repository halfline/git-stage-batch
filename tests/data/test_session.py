"""Tests for session state helpers."""

from git_stage_batch.data.session import (
    active_session_marker_path,
    session_is_active,
)


def test_active_session_marker_path_uses_git_stage_batch_state(tmp_path):
    """Active session marker path should be relative to the git directory."""
    git_dir = tmp_path / ".git"

    assert active_session_marker_path(git_dir) == (
        git_dir / "git-stage-batch" / "session" / "abort" / "head.txt"
    )


def test_session_is_active_checks_marker_without_creating_state(tmp_path):
    """Session activity checks should not create state directories."""
    git_dir = tmp_path / ".git"

    assert not session_is_active(git_dir)
    assert not git_dir.exists()

    marker_path = active_session_marker_path(git_dir)
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text("HEAD\n")

    assert session_is_active(git_dir)
