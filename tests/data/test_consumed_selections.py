"""Tests for consumed-selection ownership persistence."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.core.models import LineEntry
from git_stage_batch.data.consumed_selections import (
    read_consumed_file_metadata,
    record_consumed_selection,
)
from git_stage_batch.editor import EditorBuffer


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    return repo


def test_record_consumed_selection_refreshes_stale_first_selection(temp_git_repo):
    """Stale replacement selections should be translated in working-tree space."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("header\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

    test_file.write_text("header\nline1\n")

    command_start()
    record_consumed_selection(
        "test.txt",
        source_buffer=b"header\nline1\n",
        selected_lines=[
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"line1",
                text="line1",
                source_line=None,
            )
        ],
        replacement_mask={
            "deleted_lines": ["staged line"],
            "added_lines": ["line1"],
        },
    )

    metadata = read_consumed_file_metadata("test.txt")
    assert metadata is not None
    assert metadata["presence_claims"] == [{"source_lines": ["2"]}]
    assert metadata["replacement_masks"] == [
        {
            "deleted_lines": ["staged line"],
            "added_lines": ["line1"],
        }
    ]


def test_record_consumed_selection_accepts_buffer(temp_git_repo):
    """Consumed-selection sources can be stored from an open buffer."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("header\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

    test_file.write_text("header\nline1\n")

    command_start()
    source_buffer = EditorBuffer.from_bytes(b"header\nline1\n")
    try:
        record_consumed_selection(
            "test.txt",
            source_buffer=source_buffer,
            selected_lines=[
                LineEntry(
                    id=1,
                    kind="+",
                    old_line_number=None,
                    new_line_number=2,
                    text_bytes=b"line1",
                    text="line1",
                    source_line=None,
                )
            ],
        )
        assert source_buffer.byte_count == len(b"header\nline1\n")
    finally:
        source_buffer.close()

    metadata = read_consumed_file_metadata("test.txt")
    assert metadata is not None
    assert metadata["presence_claims"] == [{"source_lines": ["2"]}]

    result = subprocess.run(
        ["git", "show", f"{metadata['batch_source_commit']}:test.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "header\nline1\n"
