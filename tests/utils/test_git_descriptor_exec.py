"""Tests for Darwin Git object-directory descriptor execution."""

import os
from types import SimpleNamespace

import pytest

from git_stage_batch.utils import git_descriptor_exec


def test_darwin_descriptor_environment_wraps_only_git(monkeypatch):
    monkeypatch.setattr(git_descriptor_exec.sys, "platform", "darwin")
    environment = {
        git_descriptor_exec.DARWIN_OBJECT_DIRECTORY_DESCRIPTOR: "7",
    }

    wrapped = git_descriptor_exec.prepare_darwin_descriptor_command(
        ["git", "cat-file", "-t", "HEAD"],
        environment,
    )

    assert wrapped[:4] == [
        git_descriptor_exec.sys.executable,
        "-B",
        "-m",
        "git_stage_batch.utils.git_descriptor_exec",
    ]
    assert wrapped[4:] == ["--", "git", "cat-file", "-t", "HEAD"]
    with pytest.raises(ValueError, match="require a Git command"):
        git_descriptor_exec.prepare_darwin_descriptor_command(
            ["printf", "unexpected"],
            environment,
        )


def test_darwin_descriptor_path_authenticates_visible_identity(
    tmp_path,
    monkeypatch,
):
    directory = tmp_path / "objects"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        monkeypatch.setattr(
            git_descriptor_exec.fcntl,
            "F_GETPATH",
            50,
            raising=False,
        )
        monkeypatch.setattr(
            git_descriptor_exec.fcntl,
            "fcntl",
            lambda *_arguments: os.fsencode(directory)
            + b"\0"
            * (
                git_descriptor_exec._DARWIN_DESCRIPTOR_PATH_BYTES
                - len(os.fsencode(directory))
            ),
        )

        assert git_descriptor_exec._descriptor_path(descriptor) == str(directory)

        replacement = tmp_path / "replacement"
        directory.rename(replacement)
        directory.mkdir()
        with pytest.raises(RuntimeError, match="identity changed"):
            git_descriptor_exec._descriptor_path(descriptor)
    finally:
        os.close(descriptor)


def test_darwin_descriptor_runner_rewrites_child_object_paths(monkeypatch):
    observed = {}

    class FakeQueue:
        def control(self, changes, maximum_events, timeout):
            observed.setdefault("controls", []).append(
                (changes, maximum_events, timeout)
            )
            return []

        def close(self):
            observed["closed"] = True

    monkeypatch.setattr(
        git_descriptor_exec.select,
        "kqueue",
        FakeQueue,
        raising=False,
    )
    monkeypatch.setattr(
        git_descriptor_exec,
        "_descriptor_events",
        lambda descriptors: [("watch", descriptor) for descriptor in descriptors],
    )
    monkeypatch.setattr(
        git_descriptor_exec,
        "_descriptor_path",
        lambda descriptor: f"/private/object-directory-{descriptor}",
    )
    monkeypatch.setattr(
        git_descriptor_exec,
        "_open_descriptor_path_chain",
        lambda _path, descriptor: (descriptor + 10,),
    )

    def spawn(executable, arguments, environment):
        observed["spawn"] = (executable, arguments, environment)
        return 97

    monkeypatch.setattr(git_descriptor_exec.os, "posix_spawnp", spawn)
    monkeypatch.setattr(git_descriptor_exec.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        git_descriptor_exec,
        "_wait_for_child",
        lambda process_id, _queue, descriptors, watched_count: (
            observed.setdefault(
                "wait",
                (process_id, descriptors, watched_count),
            )
            and 0
        ),
    )
    monkeypatch.setattr(git_descriptor_exec.os, "close", lambda _descriptor: None)
    environment = {
        "PATH": "/usr/bin:/bin",
        "GIT_QUARANTINE_PATH": "/unsafe/quarantine",
        git_descriptor_exec.DARWIN_OBJECT_DIRECTORY_DESCRIPTOR: "7",
        git_descriptor_exec.DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR: "8",
    }

    assert git_descriptor_exec._run(["git", "hash-object", "--stdin"], environment) == 0

    executable, arguments, child_environment = observed["spawn"]
    assert executable == "git"
    assert arguments == ["git", "hash-object", "--stdin"]
    assert child_environment["GIT_OBJECT_DIRECTORY"] == "/private/object-directory-7"
    assert child_environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] == (
        "/private/object-directory-8"
    )
    assert "GIT_QUARANTINE_PATH" not in child_environment
    assert git_descriptor_exec.DARWIN_OBJECT_DIRECTORY_DESCRIPTOR not in (
        child_environment
    )
    assert git_descriptor_exec.DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR not in (
        child_environment
    )
    assert observed["wait"] == (97, (7, 8), 4)
    assert observed["closed"] is True


def test_darwin_descriptor_runner_stops_child_on_directory_event(monkeypatch):
    queue = SimpleNamespace(control=lambda *_arguments: [object()])
    terminated = []
    monkeypatch.setattr(git_descriptor_exec.os, "waitpid", lambda *_args: (0, 0))
    monkeypatch.setattr(
        git_descriptor_exec,
        "_terminate_child",
        lambda process_id: terminated.append(process_id),
    )

    with pytest.raises(RuntimeError, match="directory moved"):
        git_descriptor_exec._wait_for_child(101, queue, (7, 8), 2)

    assert terminated == [101]
