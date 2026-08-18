"""Tests for batch storage operations."""

from git_stage_batch.utils.paths import ensure_state_directory_exists

import subprocess

import pytest

from git_stage_batch.batch.state.lifecycle import create_batch
from git_stage_batch.batch.state.batch_names import batch_exists
from git_stage_batch.batch.state.query import read_batch_metadata
from git_stage_batch.batch.state.compatibility_metadata import (
    write_file_backed_batch_metadata,
)
from git_stage_batch.batch.state.references import sync_batch_state_refs
from git_stage_batch.batch.merge.merge import merge_batch_from_line_sequences_as_buffer
from tests.batch_file_helpers import read_file_from_batch
from git_stage_batch.batch.text_file_storage import (
    BatchFileUpdate,
    add_file_to_batch,
    add_files_to_batch,
    add_source_bound_file_to_batch,
)
from git_stage_batch.batch.file_state import (
    BatchMetadataRevision,
    SourceBoundOwnership,
)
from git_stage_batch.batch.source.snapshots import create_batch_source_commit
from git_stage_batch.batch.ownership.absence_content import AbsenceContentBuilder
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import (
    BatchOwnership,
)
from git_stage_batch.batch.ownership.detachment import acquire_detached_batch_ownership
import git_stage_batch.batch.ownership.detachment as detachment_module
from git_stage_batch.batch.ownership.merging import (
    _absence_signature,
    merge_batch_ownership,
)
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.batch.realization.entry_storage import RealizedEntries
import git_stage_batch.batch.ownership.absence_content as absence_content_module
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.coordinates import BatchSourceSpace, content_snapshot
from git_stage_batch.utils.git_object_io import create_git_blob
from tests.batch.ownership.metadata_helpers import acquire_ownership_for_metadata


def merge_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
) -> bytes:
    """Return merged bytes through the buffer-returning production API."""
    with (
        LineBuffer.from_bytes(batch_source_content) as source_lines,
        LineBuffer.from_bytes(working_content) as working_lines,
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


def test_add_file_to_batch_preserves_legacy_intent_marker(temp_git_repo):
    """Editing migrated ownership must not silently erase its uncertainty."""
    create_batch("test-batch", "Test")
    ownership = BatchOwnership.from_presence_lines(["1"], [])
    add_file_to_batch("test-batch", "file.txt", ownership)
    metadata = read_batch_metadata("test-batch")
    metadata["files"]["file.txt"][
        "legacy_unmarked_source_alternatives"
    ] = True
    model = write_file_backed_batch_metadata("test-batch", metadata)
    sync_batch_state_refs("test-batch", model)

    add_file_to_batch("test-batch", "file.txt", ownership)

    file_meta = read_batch_metadata("test-batch")["files"]["file.txt"]
    assert file_meta["legacy_unmarked_source_alternatives"] is True


def test_source_bound_storage_rejects_ownership_for_different_content(
    temp_git_repo,
):
    """Canonical persistence must not bless coordinates against its commit."""
    source_commit = create_batch_source_commit("file.txt")
    ownership = BatchOwnership.from_presence_lines(["1"], [])
    wrong_source = LineBuffer.from_bytes(b"other content\n")

    with wrong_source, pytest.raises(
        ValueError,
        match="coordinate snapshots do not match",
    ):
        add_source_bound_file_to_batch(
            "test-batch",
            "file.txt",
            SourceBoundOwnership(
                content_snapshot(
                    "file.txt",
                    wrong_source,
                    space=BatchSourceSpace,
                ),
                ownership,
            ),
            batch_source_commit=source_commit,
            expected_metadata_revision=BatchMetadataRevision("stale"),
        )

    assert not batch_exists("test-batch")


def test_source_bound_storage_rejects_stale_metadata_revision(
    temp_git_repo,
):
    """A prepared update must not publish under a newer metadata revision."""
    create_batch("test-batch", "Test")
    prepared_metadata = read_batch_metadata("test-batch")
    source_commit = create_batch_source_commit("file.txt")
    source = LineBuffer.from_bytes(b"line1\nline2\nline3\n")
    ownership = BatchOwnership.from_presence_lines(["1"], [])

    # Simulate an intervening canonical publication after preparation.
    add_file_to_batch(
        "test-batch",
        "file.txt",
        BatchOwnership.from_presence_lines(["2"], []),
        batch_source_commit=source_commit,
    )

    with source, pytest.raises(
        ValueError,
        match="metadata changed after ownership was prepared",
    ):
        add_source_bound_file_to_batch(
            "test-batch",
            "file.txt",
            SourceBoundOwnership(
                content_snapshot(
                    "file.txt",
                    source,
                    space=BatchSourceSpace,
                ),
                ownership,
            ),
            batch_source_commit=source_commit,
            expected_metadata_revision=BatchMetadataRevision.from_metadata(
                prepared_metadata
            ),
        )

    file_metadata = read_batch_metadata("test-batch")["files"]["file.txt"]
    assert file_metadata["presence_claims"] == [{"source_lines": ["2"]}]


def test_bulk_source_bound_storage_rejects_duplicate_paths_before_mutation(
    temp_git_repo,
):
    """A bulk update may not silently replace an earlier same-path update."""
    create_batch("test-batch", "Test")
    revision = BatchMetadataRevision.from_metadata(
        read_batch_metadata("test-batch")
    )
    source_commit = create_batch_source_commit("file.txt")
    source = LineBuffer.from_bytes(b"line1\nline2\nline3\n")

    with source:
        snapshot = content_snapshot(
            "file.txt",
            source,
            space=BatchSourceSpace,
        )
        updates = [
            BatchFileUpdate(
                file_path="file.txt",
                batch_source_commit=source_commit,
                bound_ownership=SourceBoundOwnership(
                    snapshot,
                    BatchOwnership.from_presence_lines([line], []),
                ),
                expected_metadata_revision=revision,
            )
            for line in ("1", "2")
        ]
        with pytest.raises(ValueError, match="duplicate paths"):
            add_files_to_batch("test-batch", updates)

    assert read_batch_metadata("test-batch")["files"] == {}


def test_add_file_to_batch_persists_replacement_units(temp_git_repo):
    """Text metadata should round-trip explicit replacement-unit references."""
    create_batch("test-batch", "Test")

    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old\n"]),
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

    with acquire_ownership_for_metadata(file_meta) as round_tripped:
        assert round_tripped.replacement_units == [
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ]


def test_deletion_claim_metadata_accepts_non_list_content_lines(
    temp_git_repo,
    line_sequence,
):
    """Absence claim metadata only requires indexed content lines."""
    ownership = BatchOwnership.from_presence_lines(
        [],
        [
            AbsenceClaim(
                anchor_line=None,
                content_lines=line_sequence([b"old one\n", b"old two\n"]),
            ),
        ],
    )

    metadata = ownership.to_metadata_dict()
    with acquire_ownership_for_metadata(metadata) as round_tripped:
        assert list(round_tripped.deletions[0].content_lines) == [
            b"old one\n",
            b"old two\n",
        ]


def test_absence_claim_metadata_keeps_deletions_key(temp_git_repo):
    """Serialized metadata should keep the compatible deletions key."""
    with LineBuffer.from_chunks([b"old\n"]) as content:
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                AbsenceClaim(
                    anchor_line=None,
                    content_lines=content,
                ),
            ],
        )
        metadata = ownership.to_metadata_dict()

    assert "deletions" in metadata
    assert "absence_claims" not in metadata
    assert metadata["deletions"][0]["after_source_line"] is None


def test_source_alternative_absence_claim_round_trips(temp_git_repo):
    """A retained live alternative should remain distinct from baseline loss."""
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(
                anchor_line=None,
                content_lines=[b"live alternative\n"],
                source_alternative=True,
            ),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )

    metadata = ownership.to_metadata_dict()

    assert metadata["deletions"][0]["source_alternative"] is True
    assert "source_alternative" not in (
        ownership.to_attribution_metadata_dict()["deletions"][0]
    )
    with acquire_ownership_for_metadata(metadata) as round_tripped:
        assert round_tripped.deletions[0].source_alternative is True


def test_batch_ownership_metadata_acquisition_scopes_deletion_buffers(temp_git_repo):
    """Acquired ownership should keep deletion content usable only inside."""
    ownership = BatchOwnership.from_presence_lines(
        [],
        [
            AbsenceClaim(
                anchor_line=None,
                content_lines=[b"old one\n", b"old two\n"],
            ),
        ],
    )
    metadata = ownership.to_metadata_dict()

    with acquire_ownership_for_metadata(metadata) as scoped_ownership:
        content_lines = scoped_ownership.deletions[0].content_lines
        assert isinstance(content_lines, LineBuffer)
        assert content_lines[0] == b"old one\n"
        assert content_lines[1] == b"old two\n"

    with pytest.raises(ValueError, match="buffer is closed"):
        content_lines[0]


def test_acquire_detached_batch_ownership_keeps_copied_content(temp_git_repo):
    """Detached ownership should keep copied absence content after scope."""
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(
                anchor_line=None,
                content_lines=[b"old one\n", b"old two\n"],
            ),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )
    metadata = ownership.to_metadata_dict()

    with acquire_ownership_for_metadata(metadata) as scoped_ownership:
        detached_context = acquire_detached_batch_ownership(scoped_ownership)
        content_lines = scoped_ownership.deletions[0].content_lines
        assert isinstance(content_lines, LineBuffer)

    with pytest.raises(ValueError, match="buffer is closed"):
        content_lines[0]

    with detached_context as detached:
        detached_content = detached.deletions[0].content_lines
        assert isinstance(detached_content, LineBuffer)
        assert list(detached_content) == [
            b"old one\n",
            b"old two\n",
        ]
        assert detached.presence_line_set() == {1}
        assert detached.replacement_units == [
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ]

    with pytest.raises(ValueError, match="buffer is closed"):
        detached_content.to_bytes()


def test_acquire_detached_batch_ownership_streams_buffer_content(monkeypatch):
    """Detached absence content should copy buffer bytes without line indexing."""
    def fail_getitem(self, index):
        raise AssertionError("detach should stream byte chunks")

    with LineBuffer.from_chunks([b"old one\n", b"old two\n"]) as content:
        monkeypatch.setattr(LineBuffer, "__getitem__", fail_getitem)
        detached_context = acquire_detached_batch_ownership(
            BatchOwnership.from_presence_lines(
                [],
                [AbsenceClaim(anchor_line=None, content_lines=content)],
            )
        )

    with detached_context as detached:
        detached_content = detached.deletions[0].content_lines
        assert isinstance(detached_content, LineBuffer)
        assert detached_content.to_bytes() == b"old one\nold two\n"

    with pytest.raises(ValueError, match="buffer is closed"):
        detached_content.to_bytes()


def test_acquire_detached_batch_ownership_closes_on_base_exception(monkeypatch):
    """A cancelled detach should close copies completed before cancellation."""
    copied_buffer = LineBuffer.from_bytes(b"old one\n")
    calls = 0

    def interrupt_second_copy(_content_lines):
        nonlocal calls
        calls += 1
        if calls == 1:
            return copied_buffer
        raise KeyboardInterrupt

    monkeypatch.setattr(
        detachment_module,
        "_copy_absence_content",
        interrupt_second_copy,
    )
    ownership = BatchOwnership.from_presence_lines(
        [],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old one\n"]),
            AbsenceClaim(anchor_line=None, content_lines=[b"old two\n"]),
        ],
    )

    with pytest.raises(KeyboardInterrupt):
        acquire_detached_batch_ownership(ownership)

    with pytest.raises(ValueError, match="buffer is closed"):
        copied_buffer.to_bytes()


def test_realized_entries_propagates_owned_resource_close_failure():
    """A backing resource failure must not look like deferred lease closure."""

    class FailingLineBuffer(LineBuffer):
        def close(self):
            super().close()
            raise ValueError("backing resource close failed")

    entries = RealizedEntries()
    resource = FailingLineBuffer.from_bytes(b"line\n")
    entries.retain_line_buffer(resource)

    with pytest.raises(ValueError, match="backing resource close failed"):
        entries.close()

    with pytest.raises(ValueError, match="realized entries are closed"):
        len(entries)
    with pytest.raises(ValueError, match="buffer is closed"):
        resource.to_bytes()


def test_realized_entries_retries_owned_resource_close_failure():
    """Closed entry wrappers must keep retrying a retained resource failure."""

    class RetryableLineBuffer(LineBuffer):
        close_count = 0

        def close(self):
            self.close_count += 1
            if self.close_count == 1:
                raise KeyboardInterrupt("backing resource close cancelled")
            super().close()

    entries = RealizedEntries()
    resource = RetryableLineBuffer.from_bytes(b"line\n")
    entries.retain_line_buffer(resource)

    with pytest.raises(KeyboardInterrupt, match="close cancelled"):
        entries.close()
    with pytest.raises(ValueError, match="realized entries are closed"):
        len(entries)

    entries.close()
    assert resource.close_count == 2
    with pytest.raises(ValueError, match="buffer is closed"):
        resource.to_bytes()


def test_absence_signature_streams_line_buffer_chunks(monkeypatch):
    """Absence signatures should hash buffer chunks without line indexing."""
    def fail_getitem(self, index):
        raise AssertionError("absence signature should stream byte chunks")

    with (
        LineBuffer.from_chunks([b"old one\n", b"old two\n"]) as left_content,
        LineBuffer.from_chunks([b"old one\n", b"old two\n"]) as right_content,
    ):
        monkeypatch.setattr(LineBuffer, "__getitem__", fail_getitem)
        left_claim = AbsenceClaim(anchor_line=None, content_lines=left_content)
        right_claim = AbsenceClaim(anchor_line=None, content_lines=right_content)

        signature = _absence_signature(left_claim)
        merged = merge_batch_ownership(
            BatchOwnership.from_presence_lines([], [left_claim]),
            BatchOwnership.from_presence_lines([], [right_claim]),
        )

        assert signature.byte_count == len(b"old one\nold two\n")
        assert signature.line_count == 2
        assert len(merged.deletions) == 1


def test_absence_content_builder_closes_editor_on_finish(monkeypatch):
    """Finishing absence content should close the temporary editor."""
    close_count = 0
    original_close = absence_content_module.LineEditor.close

    def count_close(self):
        nonlocal close_count
        close_count += 1
        original_close(self)

    monkeypatch.setattr(absence_content_module.LineEditor, "close", count_close)

    with AbsenceContentBuilder() as builder:
        builder.append_line_range([b"old\n"], 0, 1)
        content = builder.finish()

    try:
        assert content.to_bytes() == b"old\n"
    finally:
        content.close()
    assert close_count == 1


def test_absence_content_builder_closes_editor_on_exception(monkeypatch):
    """Failing absence construction should close the temporary editor."""
    close_count = 0
    original_close = absence_content_module.LineEditor.close

    def count_close(self):
        nonlocal close_count
        close_count += 1
        original_close(self)

    monkeypatch.setattr(absence_content_module.LineEditor, "close", count_close)

    with pytest.raises(RuntimeError, match="boom"):
        with AbsenceContentBuilder() as builder:
            builder.append_line_range([b"old\n"], 0, 1)
            raise RuntimeError("boom")

    assert close_count == 1


def test_legacy_claimed_lines_metadata_loads_as_presence_claims(temp_git_repo):
    """Old claimed_lines metadata should retain presence ownership."""
    with acquire_ownership_for_metadata({
        "claimed_lines": ["2"],
        "deletions": [],
    }) as ownership:

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

    with acquire_ownership_for_metadata({
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
    }) as ownership:
        assert ownership.presence_line_set() == {2}
        assert list(ownership.deletions[0].content_lines) == [b"old\n"]
        assert ownership.replacement_units == [
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
        ]


def test_add_file_to_batch_persists_baseline_references(temp_git_repo):
    """Presence and absence claims should share baseline reference metadata."""
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add baseline file"],
        check=True,
        capture_output=True,
    )
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
            AbsenceClaim(
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

    with acquire_ownership_for_metadata(file_meta) as round_tripped:
        assert round_tripped.presence_baseline_references()[2] == presence_reference
        assert round_tripped.deletions[0].baseline_reference == deletion_reference


def test_replacement_unit_origin_round_trips_metadata(temp_git_repo):
    """Original replacement parent context should survive metadata loading."""
    origin_reference = BaselineReference(
        after_line=1,
        after_content=b"before",
        before_line=4,
        before_content=b"after",
        has_before_line=True,
    )
    origin = ReplacementUnitOrigin(
        old_start=2,
        old_end=3,
        new_start=2,
        new_end=3,
        baseline_reference=origin_reference,
    )
    old_blob = create_git_blob([b"old\n"])

    with acquire_ownership_for_metadata({
        "presence_claims": [{"source_lines": ["2"]}],
        "deletions": [
            {
                "after_source_line": 1,
                "blob": old_blob,
            }
        ],
        "replacement_units": [
            ReplacementUnit(
                presence_lines=["2"],
                deletion_indices=[0],
                origin=origin,
            ).to_dict()
        ],
    }) as ownership:
        round_tripped_origin = ownership.replacement_units[0].origin
        assert round_tripped_origin == origin
        assert round_tripped_origin.baseline_reference.after_content == b"before"
        assert round_tripped_origin.baseline_reference.before_content == b"after"


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
            AbsenceClaim(anchor_line=None, content_lines=[b"old one\n"]),
            AbsenceClaim(anchor_line=None, content_lines=[b"old two\n"]),
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
        [AbsenceClaim(anchor_line=None, content_lines=[b"gone\n"])],
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
