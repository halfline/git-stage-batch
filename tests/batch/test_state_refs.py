"""Tests for experimental Git-backed batch state refs."""

import json
import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch, delete_batch, update_batch_note
from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.batch.state_refs import get_batch_content_ref_name, get_batch_state_ref_name
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import ensure_state_directory_exists


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for state ref tests."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    (tmp_path / "file.txt").write_text("line1\nline2\nline3\n")
    ensure_state_directory_exists()
    initialize_abort_state()

    return tmp_path


def _git_show(refspec: str) -> str:
    return subprocess.run(
        ["git", "show", refspec],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _git_rev_parse(refname: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--verify", refname],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_state_ref_contains_batch_json_and_source_snapshot(temp_git_repo):
    """State ref mirrors metadata and stores source bytes as tree entries."""
    create_batch("test-batch", "Test note")
    add_file_to_batch("test-batch", "file.txt", BatchOwnership.from_presence_lines(["1-2"], []))

    content_ref = get_batch_content_ref_name("test-batch")
    state_ref = get_batch_state_ref_name("test-batch")
    content_commit = _git_rev_parse(content_ref)

    batch_json = json.loads(_git_show(f"{state_ref}:batch.json"))
    assert batch_json["batch"] == "test-batch"
    assert batch_json["note"] == "Test note"
    assert batch_json["content_ref"] == content_ref
    assert batch_json["content_commit"] == content_commit
    assert batch_json["files"]["file.txt"]["source_path"] == "sources/file.txt"

    source_content = _git_show(f"{state_ref}:sources/file.txt")
    assert source_content == "line1\nline2\nline3\n"


def test_state_ref_updates_note_history(temp_git_repo):
    """State ref advances when metadata-only fields change."""
    create_batch("test-batch", "Before")
    first_state = _git_rev_parse(get_batch_state_ref_name("test-batch"))

    update_batch_note("test-batch", "After")
    state_ref = get_batch_state_ref_name("test-batch")
    second_state = _git_rev_parse(state_ref)

    assert second_state != first_state
    batch_json = json.loads(_git_show(f"{state_ref}:batch.json"))
    assert batch_json["note"] == "After"


def test_delete_batch_removes_experimental_refs(temp_git_repo):
    """Deleting a batch removes its mirrored content and state refs."""
    create_batch("test-batch", "Test")
    assert _git_rev_parse(get_batch_content_ref_name("test-batch"))
    assert _git_rev_parse(get_batch_state_ref_name("test-batch"))

    delete_batch("test-batch")

    for refname in (get_batch_content_ref_name("test-batch"), get_batch_state_ref_name("test-batch")):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", refname],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
