"""Tests for Git index entry lookups."""

import subprocess

import pytest

import git_stage_batch.data.index_entries as index_entries_module
from git_stage_batch.data.index_entries import (
    read_index_entries,
    read_index_entry,
    read_index_path_entries,
    read_intent_to_add_paths,
)
from git_stage_batch.data.file_target_identity import read_index_identities
from git_stage_batch.data.undo.snapshots import snapshot_current_state
from git_stage_batch.exceptions import CommandError


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository."""
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

    return repo


def test_reads_regular_file_index_entry(temp_git_repo):
    test_file = temp_git_repo / "regular.txt"
    test_file.write_text("regular\n")
    subprocess.run(["git", "add", "regular.txt"], check=True, cwd=temp_git_repo)

    entry = read_index_entry("regular.txt")

    assert entry is not None
    assert entry.mode == "100644"
    assert len(entry.object_id) == 40


def test_reads_executable_file_index_entry(temp_git_repo):
    test_file = temp_git_repo / "script.sh"
    test_file.write_text("#!/bin/sh\n")
    test_file.chmod(0o755)
    subprocess.run(["git", "add", "script.sh"], check=True, cwd=temp_git_repo)

    entry = read_index_entry("script.sh")

    assert entry is not None
    assert entry.mode == "100755"
    assert len(entry.object_id) == 40


def test_returns_none_for_missing_index_entry(temp_git_repo):
    assert read_index_entry("missing.txt") is None


def test_does_not_treat_directory_path_as_nested_file_entry(temp_git_repo):
    nested_file = temp_git_repo / "nested" / "file.txt"
    nested_file.parent.mkdir()
    nested_file.write_text("nested\n")
    subprocess.run(["git", "add", "nested/file.txt"], check=True, cwd=temp_git_repo)

    assert read_index_entry("nested") is None


def test_reads_several_scoped_index_entries_in_one_lookup(temp_git_repo):
    for path in ("one.txt", "two.txt", "unrelated.txt"):
        (temp_git_repo / path).write_text(f"{path}\n")
    subprocess.run(
        ["git", "add", "one.txt", "two.txt", "unrelated.txt"],
        check=True,
        cwd=temp_git_repo,
    )

    entries = read_index_entries(["one.txt", "missing.txt", "two.txt"])

    assert set(entries) == {"one.txt", "two.txt"}
    assert all(entry.mode == "100644" for entry in entries.values())


def test_bulk_entry_read_chunks_large_path_argument_sets(
    temp_git_repo,
    monkeypatch,
):
    """Repository-scale overlays must not depend on one oversized argv."""
    for path in ("one.txt", "two.txt", "three.txt"):
        (temp_git_repo / path).write_text(f"{path}\n")
    subprocess.run(
        ["git", "add", "one.txt", "two.txt", "three.txt"],
        check=True,
        cwd=temp_git_repo,
    )
    monkeypatch.setattr(
        index_entries_module,
        "_PATH_ARGUMENT_COUNT_LIMIT",
        1,
    )
    calls = 0
    original_run = index_entries_module.run_git_command

    def count_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_run(*args, **kwargs)

    monkeypatch.setattr(index_entries_module, "run_git_command", count_run)

    entries = read_index_entries(["one.txt", "two.txt", "three.txt"])

    assert set(entries) == {"one.txt", "two.txt", "three.txt"}
    assert calls == 3


def test_intent_to_add_read_chunks_large_path_argument_sets(
    temp_git_repo,
    monkeypatch,
):
    """Intent detection should retain exact semantics across argv chunks."""
    for path in ("one.txt", "two.txt"):
        (temp_git_repo / path).write_text(f"{path}\n")
    subprocess.run(
        ["git", "add", "-N", "one.txt", "two.txt"],
        check=True,
        cwd=temp_git_repo,
    )
    monkeypatch.setattr(
        index_entries_module,
        "_PATH_ARGUMENT_COUNT_LIMIT",
        1,
    )

    assert read_intent_to_add_paths(["one.txt", "missing.txt", "two.txt"]) == frozenset(
        {"one.txt", "two.txt"}
    )


def test_single_entry_read_propagates_index_failure(temp_git_repo):
    (temp_git_repo / ".git" / "index").write_bytes(b"invalid index")

    with pytest.raises(subprocess.CalledProcessError):
        read_index_entry("regular.txt")


def test_bulk_entry_read_propagates_index_failure(temp_git_repo):
    (temp_git_repo / ".git" / "index").write_bytes(b"invalid index")

    with pytest.raises(subprocess.CalledProcessError):
        read_index_entries(["one.txt", "two.txt"])


def _write_unmerged_index_entries(repository, file_path):
    object_ids = []
    for content in ("base\n", "ours\n", "theirs\n"):
        object_ids.append(
            subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                input=content,
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    subprocess.run(
        ["git", "update-index", "--index-info"],
        input="".join(
            f"100644 {object_id} {stage}\t{file_path}\n"
            for stage, object_id in enumerate(object_ids, start=1)
        ),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(object_ids)


def test_bulk_index_identity_preserves_unmerged_stages(temp_git_repo):
    object_ids = _write_unmerged_index_entries(temp_git_repo, "conflicted.txt")

    entries = read_index_path_entries(["conflicted.txt", "missing.txt"])
    identities = read_index_identities(["conflicted.txt", "missing.txt"])

    assert entries["conflicted.txt"].stage_zero is None
    assert tuple(
        (entry.stage, entry.object_id)
        for entry in entries["conflicted.txt"].unmerged_entries
    ) == tuple(enumerate(object_ids, start=1))
    assert identities["conflicted.txt"].unmerged_entries
    assert identities["missing.txt"].unmerged_entries == ()


def test_bulk_index_identity_distinguishes_intent_from_staged_empty_file(
    temp_git_repo,
):
    for file_path in ("intent.txt", "staged-empty.txt"):
        (temp_git_repo / file_path).write_text("")
    subprocess.run(
        ["git", "add", "-N", "intent.txt"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "staged-empty.txt"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    identities = read_index_identities(["intent.txt", "staged-empty.txt"])

    assert (
        identities["intent.txt"].object_id == identities["staged-empty.txt"].object_id
    )
    assert identities["intent.txt"].intent_to_add is True
    assert identities["intent.txt"].content_object_id is None
    assert identities["staged-empty.txt"].intent_to_add is False
    assert identities["staged-empty.txt"].content_object_id is not None


def test_gitlink_intent_detection_ignores_repository_diff_suppression(
    temp_git_repo,
):
    """A user's ignoreSubmodules setting must not hide a gitlink ITA flag."""
    submodule = temp_git_repo / "sub"
    submodule.mkdir()
    subprocess.run(["git", "init"], cwd=submodule, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=submodule,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=submodule,
        check=True,
        capture_output=True,
    )
    (submodule / "file.txt").write_text("sub\n")
    subprocess.run(
        ["git", "add", "file.txt"], cwd=submodule, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Add sub file"],
        cwd=submodule,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "-N", "sub"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "diff.ignoreSubmodules", "all"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    identity = read_index_identities(("sub",))["sub"]

    assert identity.mode == "160000"
    assert identity.intent_to_add is True
    assert identity.content_object_id is None


def test_checkpoint_capture_refuses_unmerged_scoped_index_path(temp_git_repo):
    _write_unmerged_index_entries(temp_git_repo, "conflicted.txt")

    with pytest.raises(CommandError, match="unmerged entries: conflicted.txt"):
        snapshot_current_state([], index_paths=["conflicted.txt"])
