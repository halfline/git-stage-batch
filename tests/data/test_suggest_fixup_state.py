"""Tests for canonical persisted fixup-suggestion state."""

import json
import subprocess

import pytest

from git_stage_batch.data.suggest_fixup_state import (
    SUGGEST_FIXUP_STATE_SCHEMA_VERSION,
    clear_suggest_fixup_state,
    read_suggest_fixup_state,
    suggest_fixup_state_matches_search,
    write_suggest_fixup_state,
)
from git_stage_batch.utils.paths import get_suggest_fixup_state_file_path
from git_stage_batch.utils.paths import ensure_state_directory_exists


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for state-path tests."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    subprocess.run(["git", "init", "-q"], check=True, cwd=repo)
    ensure_state_directory_exists()
    get_suggest_fixup_state_file_path().parent.mkdir(parents=True, exist_ok=True)
    return repo


def _search_state():
    return {
        "schema_version": SUGGEST_FIXUP_STATE_SCHEMA_VERSION,
        "object_format": "sha1",
        "hunk_hash": "abc123",
        "line_id_ranges": [[1, 3], [5, 5]],
        "base_commit": "1" * 40,
        "head_commit": "2" * 40,
        "range_fingerprint": "3" * 64,
        "file_path": "test.py",
        "unit_id": "4" * 64,
        "queried_ranges": [[10, 12], [20, 20]],
    }


def test_read_state_when_no_file_exists(temp_git_repo):
    assert read_suggest_fixup_state() is None


def test_write_and_read_state(temp_git_repo):
    state = {
        **_search_state(),
        "last_shown_commit": "5" * 40,
        "iteration": 2,
    }

    write_suggest_fixup_state(state)

    assert read_suggest_fixup_state() == state


def test_clear_state(temp_git_repo):
    write_suggest_fixup_state(_search_state())

    clear_suggest_fixup_state()

    assert not get_suggest_fixup_state_file_path().exists()
    assert read_suggest_fixup_state() is None


def test_legacy_spelling_based_state_is_invalid(temp_git_repo):
    get_suggest_fixup_state_file_path().write_text(
        json.dumps(
            {
                "hunk_hash": "hash1",
                "line_ids": [1, 2],
                "boundary": "HEAD~2",
                "file_path": "test.py",
                "min_line": 10,
                "max_line": 20,
            }
        )
    )

    assert read_suggest_fixup_state() is None


@pytest.mark.parametrize(
    ("field", "changed_value"),
    (
        ("hunk_hash", "different"),
        ("object_format", "sha256"),
        ("line_id_ranges", [[1, 1]]),
        ("base_commit", "6" * 40),
        ("head_commit", "7" * 40),
        ("range_fingerprint", "8" * 64),
        ("file_path", "other.py"),
        ("unit_id", "9" * 64),
        ("queried_ranges", [[10, 10]]),
    ),
)
def test_search_match_covers_every_frozen_field(
    temp_git_repo,
    field,
    changed_value,
):
    search = _search_state()
    write_suggest_fixup_state(search)
    state = read_suggest_fixup_state()
    assert suggest_fixup_state_matches_search(state, search)

    changed_search = {**search, field: changed_value}

    assert not suggest_fixup_state_matches_search(state, changed_search)


@pytest.mark.parametrize(
    "ranges",
    (
        [[0, 1]],
        [[3, 2]],
        [[1, 2], [3, 4]],
        [[2, 3], [1, 1]],
    ),
)
def test_read_rejects_noncanonical_ranges(temp_git_repo, ranges):
    state = {**_search_state(), "queried_ranges": ranges}
    get_suggest_fixup_state_file_path().write_text(json.dumps(state))

    assert read_suggest_fixup_state() is None


def test_read_rejects_object_ids_with_the_wrong_format_length(temp_git_repo):
    state = {
        **_search_state(),
        "object_format": "sha256",
    }
    get_suggest_fixup_state_file_path().write_text(json.dumps(state))

    assert read_suggest_fixup_state() is None
