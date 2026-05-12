"""Tests for batch storage operations."""

from git_stage_batch.utils.paths import ensure_state_directory_exists

import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.batch.merge import merge_batch_from_line_sequences_as_buffer
from git_stage_batch.batch.storage import add_file_to_batch, get_batch_diff, read_file_from_batch
from git_stage_batch.batch.ownership import (
    BaselineReference,
    BatchOwnership,
    DeletionClaim,
    ReplacementUnit,
    detach_batch_ownership,
)
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.editor import EditorBuffer
from git_stage_batch.utils.git import create_git_blob


def merge_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
) -> bytes:
    """Return merged bytes through the buffer-returning production API."""
    with (
        EditorBuffer.from_bytes(batch_source_content) as source_lines,
        EditorBuffer.from_bytes(working_content) as working_lines,
        merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
        ) as buffer,
    ):
        return buffer.to_bytes()


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    # Create initial commit
    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    # Create a file for testing
    (tmp_path / "file.txt").write_text("line1\nline2\nline3\n")

    # Initialize abort state (needed for batch source creation)
    ensure_state_directory_exists()
    initialize_abort_state()

    return tmp_path


def test_add_file_to_batch_creates_batch(temp_git_repo):
    """Test that add_file_to_batch auto-creates batch if needed."""
    # Claim lines 1-2 from file.txt (range string format)
    ownership = BatchOwnership.from_presence_lines(["1-2"], [])
    add_file_to_batch("test-batch", "file.txt", ownership)

    content = read_file_from_batch("test-batch", "file.txt")
    assert content is not None
    assert "line1" in content
    assert "line2" in content


def test_add_file_to_batch_existing_batch(temp_git_repo):
    """Test adding file to existing batch."""
    create_batch("test-batch", "Test")

    # Claim line 1
    ownership = BatchOwnership.from_presence_lines(["1"], [])
    add_file_to_batch("test-batch", "file.txt", ownership)

    content = read_file_from_batch("test-batch", "file.txt")
    assert content is not None
    assert "line1" in content


def test_add_file_to_batch_persists_replacement_units(temp_git_repo):
    """Text metadata should round-trip explicit replacement-unit references."""
    create_batch("test-batch", "Test")

    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            DeletionClaim(anchor_line=None, content_lines=[b"old\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )

    add_file_to_batch("test-batch", "file.txt", ownership)

    file_meta = read_batch_metadata("test-batch")["files"]["file.txt"]
    assert file_meta["replacement_units"] == [
        {"presence_lines": ["1"], "deletion_indices": [0]},
    ]

    round_tripped = BatchOwnership.from_metadata_dict(file_meta)
    assert round_tripped.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
    ]


def test_deletion_claim_metadata_accepts_non_list_content_lines(
    temp_git_repo,
    line_sequence,
):
    """Deletion claim metadata only requires indexed content lines."""
    ownership = BatchOwnership.from_presence_lines(
        [],
        [
            DeletionClaim(
                anchor_line=None,
                content_lines=line_sequence([b"old one\n", b"old two\n"]),
            ),
        ],
    )

    metadata = ownership.to_metadata_dict()
    round_tripped = BatchOwnership.from_metadata_dict(metadata)

    assert round_tripped.deletions[0].content_lines == [
        b"old one\n",
        b"old two\n",
    ]


def test_batch_ownership_metadata_acquisition_scopes_deletion_buffers(temp_git_repo):
    """Acquired ownership should keep deletion content usable only inside."""
    ownership = BatchOwnership.from_presence_lines(
        [],
        [
            DeletionClaim(
                anchor_line=None,
                content_lines=[b"old one\n", b"old two\n"],
            ),
        ],
    )
    metadata = ownership.to_metadata_dict()

    with BatchOwnership.acquire_for_metadata_dict(metadata) as scoped_ownership:
        content_lines = scoped_ownership.deletions[0].content_lines
        assert isinstance(content_lines, EditorBuffer)
        assert content_lines[0] == b"old one\n"
        assert content_lines[1] == b"old two\n"

    with pytest.raises(ValueError, match="buffer is closed"):
        content_lines[0]


def test_detach_batch_ownership_keeps_acquired_deletion_content(temp_git_repo):
    """Detached ownership should keep acquired deletion content after scope."""
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            DeletionClaim(
                anchor_line=None,
                content_lines=[b"old one\n", b"old two\n"],
            ),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )
    metadata = ownership.to_metadata_dict()

    with BatchOwnership.acquire_for_metadata_dict(metadata) as scoped_ownership:
        detached = detach_batch_ownership(scoped_ownership)
        content_lines = scoped_ownership.deletions[0].content_lines
        assert isinstance(content_lines, EditorBuffer)

    with pytest.raises(ValueError, match="buffer is closed"):
        content_lines[0]

    assert detached.deletions[0].content_lines == [
        b"old one\n",
        b"old two\n",
    ]
    assert detached.presence_line_set() == {1}
    assert detached.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
    ]


def test_legacy_claimed_lines_metadata_loads_as_presence_claims(temp_git_repo):
    """Old claimed_lines metadata should retain presence ownership."""
    ownership = BatchOwnership.from_metadata_dict({
        "claimed_lines": ["2"],
        "deletions": [],
    })

    assert ownership.presence_line_set() == {2}
    assert ownership.presence_claims[0].source_lines == ["2"]

    result = merge_batch(
        b"line1\nline2\nline3\n",
        ownership,
        b"line1\nline3\n",
    )
    assert result == b"line1\nline2\nline3\n"


def test_legacy_replacement_units_metadata_loads_presence_lines(temp_git_repo):
    """Old replacement-unit keys should stay readable after upgrade."""
    old_blob = create_git_blob([b"old\n"])

    ownership = BatchOwnership.from_metadata_dict({
        "claimed_lines": ["2"],
        "deletions": [
            {
                "after_source_line": 1,
                "blob": old_blob,
            }
        ],
        "replacement_units": [
            {
                "claimed_lines": ["2"],
                "deletion_indices": [0],
            }
        ],
    })

    assert ownership.replacement_units == [
        ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
    ]


def test_add_file_to_batch_persists_baseline_references(temp_git_repo):
    """Presence and absence claims should share baseline reference metadata."""
    create_batch("test-batch", "Test")

    presence_reference = BaselineReference(
        after_line=1,
        after_content=b"line1",
        before_line=3,
        before_content=b"line3",
        has_before_line=True,
    )
    deletion_reference = BaselineReference(after_line=1)
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            DeletionClaim(
                anchor_line=1,
                content_lines=[b"old line\n"],
                baseline_reference=deletion_reference,
            ),
        ],
        baseline_references={2: presence_reference},
    )

    add_file_to_batch("test-batch", "file.txt", ownership)

    file_meta = read_batch_metadata("test-batch")["files"]["file.txt"]
    assert file_meta["presence_claims"][0]["baseline_references"]["2"][
        "after_line"
    ] == 1
    assert file_meta["deletions"][0]["baseline_reference"] == {
        "after_line": 1,
    }

    round_tripped = BatchOwnership.from_metadata_dict(file_meta)
    assert round_tripped.presence_baseline_references()[2] == presence_reference
    assert round_tripped.deletions[0].baseline_reference == deletion_reference


def test_empty_replacement_units_are_omitted_from_metadata():
    """Empty replacement-unit references should not serialize an empty key."""
    ownership = BatchOwnership.from_presence_lines(
        [],
        [],
        replacement_units=[
            ReplacementUnit(presence_lines=[], deletion_indices=[]),
        ],
    )

    assert "replacement_units" not in ownership.to_metadata_dict()


def test_boolean_replacement_unit_indices_are_omitted_from_metadata(temp_git_repo):
    """JSON booleans should not serialize as replacement deletion indexes."""
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            DeletionClaim(anchor_line=None, content_lines=[b"old one\n"]),
            DeletionClaim(anchor_line=None, content_lines=[b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[True]),
        ],
    )

    assert "replacement_units" not in ownership.to_metadata_dict()


def test_add_file_to_batch_marks_whole_added_empty_text_file(temp_git_repo):
    """Whole empty added text files need path lifecycle metadata."""
    empty_file = temp_git_repo / "empty.txt"
    empty_file.write_text("")

    ownership = BatchOwnership.from_presence_lines([], [])
    add_file_to_batch("test-batch", "empty.txt", ownership)

    file_meta = read_batch_metadata("test-batch")["files"]["empty.txt"]
    assert file_meta["change_type"] == "added"
    assert read_file_from_batch("test-batch", "empty.txt") == ""


def test_add_file_to_batch_does_not_mark_partial_added_text_file_as_lifecycle(temp_git_repo):
    """Partial line batches from a new file should stay content-scoped."""
    partial_file = temp_git_repo / "partial.txt"
    partial_file.write_text("one\ntwo\n")

    ownership = BatchOwnership.from_presence_lines(["1"], [])
    add_file_to_batch("test-batch", "partial.txt", ownership)

    file_meta = read_batch_metadata("test-batch")["files"]["partial.txt"]
    assert "change_type" not in file_meta
    assert read_file_from_batch("test-batch", "partial.txt") == "one\n"


def test_add_file_to_batch_marks_whole_deleted_text_file(temp_git_repo):
    """Whole deleted text files need deletion metadata and no batch-tree path."""
    gone_file = temp_git_repo / "gone.txt"
    gone_file.write_text("gone\n")
    subprocess.run(["git", "add", "gone.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add gone"], check=True, capture_output=True)
    initialize_abort_state()

    gone_file.unlink()
    ownership = BatchOwnership.from_presence_lines(
        [],
        [DeletionClaim(anchor_line=None, content_lines=[b"gone\n"])],
    )
    add_file_to_batch("test-batch", "gone.txt", ownership)

    file_meta = read_batch_metadata("test-batch")["files"]["gone.txt"]
    assert file_meta["change_type"] == "deleted"
    assert read_file_from_batch("test-batch", "gone.txt") is None


def test_add_file_to_batch_update_file(temp_git_repo):
    """Test updating existing file in batch."""
    create_batch("test-batch", "Test")

    # First add line 1
    ownership1 = BatchOwnership.from_presence_lines(["1"], [])
    add_file_to_batch("test-batch", "file.txt", ownership1)

    # Then update to lines 1-2
    ownership2 = BatchOwnership.from_presence_lines(["1-2"], [])
    add_file_to_batch("test-batch", "file.txt", ownership2)

    content = read_file_from_batch("test-batch", "file.txt")
    assert content is not None
    assert "line1" in content
    assert "line2" in content


def test_add_file_to_batch_multiple_files(temp_git_repo):
    """Test adding multiple files to batch."""
    # Create another test file
    (temp_git_repo / "file2.txt").write_text("other1\nother2\n")

    create_batch("test-batch", "Test")

    ownership1 = BatchOwnership.from_presence_lines(["1"], [])
    add_file_to_batch("test-batch", "file.txt", ownership1)

    ownership2 = BatchOwnership.from_presence_lines(["1"], [])
    add_file_to_batch("test-batch", "file2.txt", ownership2)

    content1 = read_file_from_batch("test-batch", "file.txt")
    content2 = read_file_from_batch("test-batch", "file2.txt")

    assert content1 is not None and "line1" in content1
    assert content2 is not None and "other1" in content2


def test_read_file_from_batch_nonexistent_batch(temp_git_repo):
    """Test reading file from nonexistent batch returns None."""
    content = read_file_from_batch("nonexistent", "file.txt")
    assert content is None


def test_read_file_from_batch_nonexistent_file(temp_git_repo):
    """Test reading nonexistent file from batch returns None."""
    create_batch("test-batch", "Test")

    content = read_file_from_batch("test-batch", "nonexistent.txt")
    assert content is None


def test_get_batch_diff_empty_batch(temp_git_repo):
    """Test getting diff for empty batch shows no changes."""
    create_batch("test-batch", "Test")

    diff = get_batch_diff("test-batch")
    # Empty batch starts with HEAD's tree, so no diff (same as baseline)
    assert diff == b""


def test_get_batch_diff_with_file(temp_git_repo):
    """Test getting diff for batch with file."""
    create_batch("test-batch", "Test")

    # Claim lines from file.txt
    ownership = BatchOwnership.from_presence_lines(["1-2"], [])
    add_file_to_batch("test-batch", "file.txt", ownership)

    diff = get_batch_diff("test-batch")
    assert b"file.txt" in diff
    assert b"+line" in diff  # Should show added lines


def test_get_batch_diff_nonexistent_batch(temp_git_repo):
    """Test getting diff for nonexistent batch returns empty string."""
    diff = get_batch_diff("nonexistent")
    assert diff == b""


def test_get_batch_diff_custom_context(temp_git_repo):
    """Test getting diff with custom context lines."""
    create_batch("test-batch", "Test")

    ownership = BatchOwnership.from_presence_lines(["1-3"], [])
    add_file_to_batch("test-batch", "file.txt", ownership)

    diff = get_batch_diff("test-batch", context_lines=1)
    assert b"file.txt" in diff
