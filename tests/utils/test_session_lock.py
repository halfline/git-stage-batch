"""Tests for repository session-lock handoff behavior."""

import fcntl
import os
import subprocess
import sys
import time

import pytest

from git_stage_batch.utils.command import start_command
from git_stage_batch.utils.session_lock import (
    SessionLockChangedDuringPrompt,
    acquire_session_lock,
    acquire_session_lock_descriptor,
    current_session_lock_generation,
    read_session_lock_generation_if_available,
    temporarily_release_session_lock,
)
from git_stage_batch.utils.paths import get_session_lock_file_path


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


def test_lock_generation_can_be_probed_without_waiting(lock_git_repo):
    """Cache readers should distinguish a free lock from a current owner."""
    assert read_session_lock_generation_if_available() == 0

    with acquire_session_lock():
        generation = current_session_lock_generation()
        assert generation == 1
        assert read_session_lock_generation_if_available() is None

    assert read_session_lock_generation_if_available() == generation


def test_lock_generation_probe_bounds_corrupt_input(lock_git_repo):
    """A corrupt lock file must not cause an unbounded read."""
    lock_path = get_session_lock_file_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("9" * 1000)

    assert read_session_lock_generation_if_available() == 0
    with acquire_session_lock():
        assert current_session_lock_generation() == 1


def test_lock_generation_probe_rejects_non_regular_file(lock_git_repo):
    """The nonblocking probe must not open a FIFO as session state."""
    lock_path = get_session_lock_file_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(lock_path)

    assert read_session_lock_generation_if_available() is None


def test_prompt_handoff_detects_intervening_lock_holder(lock_git_repo):
    """A prompt response is stale after another lock holder runs."""
    with acquire_session_lock():
        with pytest.raises(SessionLockChangedDuringPrompt):
            with temporarily_release_session_lock():
                with acquire_session_lock():
                    pass


def test_inherited_descriptor_keeps_lock_after_parent_crash(lock_git_repo):
    """An orphaned child should retain the parent session lock until exit."""
    script = "\n".join(
        (
            "import os, sys",
            "from git_stage_batch.utils.command import start_command",
            "from git_stage_batch.utils.session_lock import "
            "acquire_session_lock_descriptor",
            "with acquire_session_lock_descriptor() as descriptor:",
            "    start_command(",
            "        [sys.executable, '-c', 'import time; time.sleep(0.75)'],",
            "        pass_fds=(descriptor,),",
            "    )",
            "    os._exit(0)",
        )
    )
    parent = subprocess.run(
        [sys.executable, "-c", script],
        cwd=lock_git_repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert parent.returncode == 0, parent.stderr

    lock_path = get_session_lock_file_path()
    lock_descriptor = os.open(lock_path, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    pytest.fail("orphaned child did not release the session lock")
                time.sleep(0.02)
    finally:
        os.close(lock_descriptor)


def test_acquired_session_lock_descriptor_is_borrowed(lock_git_repo):
    """The descriptor context owns and closes its repository lock handle."""
    with acquire_session_lock_descriptor() as descriptor:
        assert descriptor >= 3
        os.fstat(descriptor)

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_live_child_keeps_lock_after_descriptor_context_exits(lock_git_repo):
    """Last-close semantics should protect a child beyond its lend context."""
    with acquire_session_lock_descriptor() as descriptor:
        process = start_command(
            [sys.executable, "-c", "import time; time.sleep(0.4)"],
            pass_fds=(descriptor,),
        )

    lock_descriptor = os.open(get_session_lock_file_path(), os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert process.wait() == 0
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        process.close()
        os.close(lock_descriptor)


def test_lent_descriptor_avoids_closed_standard_slots(lock_git_repo):
    """Child lending must work when the lock handle occupies fd zero."""
    marker = lock_git_repo / "lent-descriptor"
    script = "\n".join(
        (
            "import os",
            "from git_stage_batch.utils.session_lock import "
            "acquire_session_lock_descriptor",
            "for standard_fd in range(3):",
            "    try:",
            "        os.close(standard_fd)",
            "    except OSError:",
            "        pass",
            "with acquire_session_lock_descriptor() as descriptor:",
            f"    open({str(marker)!r}, 'w').write(str(descriptor))",
        )
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=lock_git_repo,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )

    assert result.returncode == 0
    assert int(marker.read_text()) >= 3


def test_prompt_handoff_rejects_inherited_lock_descriptor(lock_git_repo):
    """Prompt handoff must not unlock a descriptor held by a child."""
    with acquire_session_lock_descriptor():
        with pytest.raises(
            RuntimeError,
            match="while a child inherits it",
        ):
            with temporarily_release_session_lock():
                pass


def test_outer_lock_remembers_a_child_after_lend_context_exits(lock_git_repo):
    """An outer action must not unlock a child that may still be running."""
    with acquire_session_lock():
        with acquire_session_lock_descriptor() as descriptor:
            process = start_command(
                [sys.executable, "-c", "import time; time.sleep(0.4)"],
                pass_fds=(descriptor,),
            )
        try:
            with pytest.raises(
                RuntimeError,
                match="while a child inherits it",
            ):
                with temporarily_release_session_lock():
                    pass
            assert process.wait() == 0
        finally:
            process.close()
