"""Tests for Git index entry lookups."""

import subprocess

import pytest

from git_stage_batch.data.index_entries import read_index_entries, read_index_entry


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
