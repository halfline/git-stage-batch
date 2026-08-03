"""Tests for consumed-selection ownership persistence."""

from __future__ import annotations

from contextlib import contextmanager
import subprocess

import pytest

from git_stage_batch.commands.selection.consumed_selection_recording import (
    record_consumed_selection,
)
from git_stage_batch.commands.selection import consumed_selection_recording
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.source.advancement import BatchSourceAdvanceError
from git_stage_batch.commands.start import command_start
from git_stage_batch.core.models import LineEntry
from git_stage_batch.data.consumed_selections import (
    load_consumed_selections_metadata,
    read_consumed_file_metadata,
)
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import get_session_consumed_selections_file_path
from git_stage_batch.core.buffer import LineBuffer
from tests.batch.ownership.metadata_helpers import acquire_ownership_for_metadata


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
    with LineBuffer.from_bytes(b"header\nline1\n") as source_buffer:
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


def test_consumed_replacement_uses_complete_hunk_coordinates(temp_git_repo):
    """A shifted deletion anchor must use preceding unselected hunk context."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("staged\ntop\nold\n")
    subprocess.run(
        ["git", "add", "test.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add shifted replacement"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    test_file.write_text("top\nnew\n")
    command_start()
    context = LineEntry(
        id=None,
        kind=" ",
        old_line_number=2,
        new_line_number=1,
        text_bytes=b"top",
        text="top",
        source_line=None,
    )
    deletion = LineEntry(
        id=1,
        kind="-",
        old_line_number=3,
        new_line_number=None,
        text_bytes=b"old",
        text="old",
        source_line=None,
    )
    addition = LineEntry(
        id=2,
        kind="+",
        old_line_number=None,
        new_line_number=2,
        text_bytes=b"new",
        text="new",
        source_line=None,
    )

    with LineBuffer.from_bytes(b"top\nnew\n") as source_buffer:
        record_consumed_selection(
            "test.txt",
            source_buffer=source_buffer,
            selected_lines=[deletion, addition],
            coordinate_lines=[context, deletion, addition],
        )

    metadata = read_consumed_file_metadata("test.txt")
    assert metadata is not None
    with acquire_ownership_for_metadata(metadata) as ownership:
        assert ownership.presence_line_set() == {2}
        assert ownership.deletions[0].anchor_line == 1
        assert list(ownership.deletions[0].content_lines) == [b"old\n"]


def test_corrupt_consumed_selection_state_fails_closed(temp_git_repo):
    """Corrupt masking state must not make consumed lines visible again."""
    get_session_consumed_selections_file_path().parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    get_session_consumed_selections_file_path().write_text("{not-json")

    with pytest.raises(CommandError, match="Consumed-selection state is corrupt"):
        load_consumed_selections_metadata()


def test_corrupt_consumed_file_entry_fails_closed(temp_git_repo):
    """A malformed file entry must not make consumed lines visible again."""
    get_session_consumed_selections_file_path().parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    get_session_consumed_selections_file_path().write_text(
        '{"files": {"tracked.txt": "corrupt"}}'
    )

    with pytest.raises(CommandError, match="invalid entry for tracked.txt"):
        load_consumed_selections_metadata()


def test_expected_source_advance_refusal_is_a_command_error(monkeypatch):
    """Consumed-selection advancement refusals should not escape as internals."""

    @contextmanager
    def acquired_ownership(_metadata):
        yield BatchOwnership([], [])

    monkeypatch.setattr(
        consumed_selection_recording,
        "read_consumed_file_metadata",
        lambda _file_path: {"batch_source_commit": "old-source"},
    )
    monkeypatch.setattr(
        consumed_selection_recording,
        "acquire_ownership_for_metadata_dict",
        acquired_ownership,
    )
    monkeypatch.setattr(
        consumed_selection_recording,
        "read_git_object_buffer_or_none",
        lambda _object_name: LineBuffer.from_bytes(b"old\n"),
    )
    monkeypatch.setattr(
        consumed_selection_recording,
        "map_selection_to_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        consumed_selection_recording,
        "advance_batch_source_for_file_with_provenance",
        lambda **_kwargs: (_ for _ in ()).throw(
            BatchSourceAdvanceError("ambiguous replacement")
        ),
    )

    with (
        LineBuffer.from_bytes(b"content\n") as source_buffer,
        pytest.raises(CommandError, match="saved source cannot be advanced"),
    ):
        record_consumed_selection(
            "file.txt",
            source_buffer=source_buffer,
            selected_lines=[],
        )


def test_existing_consumed_source_replaces_wrong_cached_coordinates(temp_git_repo):
    """Consumed ownership is persisted in its own durable source space."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("prefix\n")
    subprocess.run(
        ["git", "add", "test.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    test_file.write_text("prefix\nselected\n")
    command_start(quiet=True)

    with LineBuffer.from_bytes(b"prefix\nselected\n") as source_buffer:
        record_consumed_selection(
            "test.txt",
            source_buffer=source_buffer,
            selected_lines=[
                LineEntry(
                    id=1,
                    kind="+",
                    old_line_number=None,
                    new_line_number=2,
                    text_bytes=b"selected",
                    source_line=2,
                )
            ],
        )
    with LineBuffer.from_bytes(b"prefix\nselected\n") as source_buffer:
        record_consumed_selection(
            "test.txt",
            source_buffer=source_buffer,
            selected_lines=[
                LineEntry(
                    id=1,
                    kind="+",
                    old_line_number=None,
                    new_line_number=2,
                    text_bytes=b"selected",
                    source_line=1,
                )
            ],
        )

    metadata = read_consumed_file_metadata("test.txt")
    assert metadata is not None
    assert metadata["presence_claims"] == [{"source_lines": ["2"]}]


def test_initial_consumed_source_remaps_equal_content_at_wrong_coordinate(
    temp_git_repo,
):
    """The first consumed source must also ignore foreign cached coordinates."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("base\n")
    subprocess.run(
        ["git", "add", "test.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    source_content = b"same\nmiddle\nsame\n"
    test_file.write_bytes(source_content)
    command_start(quiet=True)

    with LineBuffer.from_bytes(source_content) as source_buffer:
        record_consumed_selection(
            "test.txt",
            source_buffer=source_buffer,
            selected_lines=[
                LineEntry(
                    id=1,
                    kind="+",
                    old_line_number=None,
                    new_line_number=3,
                    text_bytes=b"same",
                    source_line=1,
                )
            ],
        )

    metadata = read_consumed_file_metadata("test.txt")
    assert metadata is not None
    assert metadata["presence_claims"] == [{"source_lines": ["3"]}]


def test_record_consumed_selection_accepts_buffer(temp_git_repo):
    """Consumed-selection sources can be stored from an open buffer."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("header\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

    test_file.write_text("header\nline1\n")

    command_start()
    source_buffer = LineBuffer.from_bytes(b"header\nline1\n")
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


def test_record_consumed_selection_rewrites_existing_deletions(temp_git_repo):
    """Existing consumed deletions should remain serializable when updated."""
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("old\nkeep\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

    test_file.write_text("keep\n")

    command_start()
    with LineBuffer.from_bytes(b"keep\n") as source_buffer:
        record_consumed_selection(
            "test.txt",
            source_buffer=source_buffer,
            selected_lines=[
                LineEntry(
                    id=1,
                    kind="-",
                    old_line_number=1,
                    new_line_number=None,
                    text_bytes=b"old",
                    text="old",
                    source_line=None,
                )
            ],
        )
    with LineBuffer.from_bytes(b"keep\n") as source_buffer:
        record_consumed_selection(
            "test.txt",
            source_buffer=source_buffer,
            selected_lines=[
                LineEntry(
                    id=2,
                    kind="+",
                    old_line_number=None,
                    new_line_number=1,
                    text_bytes=b"keep",
                    text="keep",
                    source_line=1,
                )
            ],
        )

    metadata = read_consumed_file_metadata("test.txt")
    assert metadata is not None
    with acquire_ownership_for_metadata(metadata) as ownership:
        assert ownership.presence_line_set() == {1}
        assert list(ownership.deletions[0].content_lines) == [b"old\n"]
