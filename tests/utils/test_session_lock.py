"""Tests for repository session-lock handoff behavior."""

import subprocess

import pytest

from git_stage_batch.utils.session_lock import (
    SessionLockChangedDuringPrompt,
    acquire_session_lock,
    temporarily_release_session_lock,
)


@pytest.fixture
def lock_git_repo(tmp_path, monkeypatch):
    """Create a repository whose common state owns the test lock."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_prompt_handoff_without_intervening_lock_holder(lock_git_repo):
    """Reacquiring an uncontended prompt lock preserves the action lease."""
    with acquire_session_lock():
        with temporarily_release_session_lock():
            pass


def test_prompt_handoff_detects_intervening_lock_holder(lock_git_repo):
    """A prompt response is stale after another lock holder runs."""
    with acquire_session_lock():
        with pytest.raises(SessionLockChangedDuringPrompt):
            with temporarily_release_session_lock():
                with acquire_session_lock():
                    pass
