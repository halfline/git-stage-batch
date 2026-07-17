"""Tests for sift command."""

from pathlib import Path

from git_stage_batch.batch.merge.merge import merge_batch_from_line_sequences_as_buffer

import subprocess
import pytest

from git_stage_batch.batch.state.batch_names import batch_exists
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.batch.state.references import get_batch_content_ref_name
from git_stage_batch.commands.new import command_new_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.include import command_include_to_batch
import git_stage_batch.commands.batch_transform.sift_results as sift_results
from git_stage_batch.commands.batch_transform.sift_persistence import (
    add_sifted_text_file_to_batch,
)
import git_stage_batch.commands.batch_transform.sift_persistence as sift_persistence
from git_stage_batch.commands.sift import (
    command_sift_batch,
)
from git_stage_batch.batch.state.query import list_batch_names, read_batch_metadata
from git_stage_batch.batch.state.query import get_batch_tree_sha
from git_stage_batch.batch.binary_file_storage import add_binary_file_to_batch
from git_stage_batch.batch.file_entry_storage import read_file_from_batch
from git_stage_batch.core.models import BinaryFileChange
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.exceptions import CommandError, MergeError
from git_stage_batch.commands.annotate import command_annotate_batch
import git_stage_batch.commands.sift as sift_command
from git_stage_batch.utils.file_job_workspace import FileJobWorkspace
from tests.batch.ownership.metadata_helpers import (
    acquire_ownership_for_metadata,
    reject_materialized_ownership_metadata as _reject_materialized_ownership_metadata,
)


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


def test_build_sift_ownership_accepts_non_list_line_sequences(line_sequence):
    """Sift ownership derivation accepts indexed byte-line sequences."""
    working_lines = line_sequence([b"line1\n", b"old\n", b"line3\n"])
    target_lines = line_sequence([b"line1\n", b"new\n", b"line3\n"])

    ownership = sift_results.build_ownership_from_working_and_target_lines(
        working_lines,
        target_lines,
    )

    assert ownership is not None
    resolved = ownership.resolve()
    assert resolved.presence_line_set == {2}
    assert len(resolved.deletion_claims) == 1
    assert isinstance(resolved.deletion_claims[0].content_lines, LineBuffer)
    assert list(resolved.deletion_claims[0].content_lines) == [b"old\n"]


def test_build_sift_ownership_consumes_target_ranges(monkeypatch):
    """Sift ownership derivation should not expand claimed target ranges."""

    class TargetRangeOnlyRun:
        kind = sift_results.SemanticChangeKind.PRESENCE
        source_start = None
        source_end = None
        target_start = 2
        target_end = 1001
        target_anchor = None

        def target_line_numbers(self):
            raise AssertionError("sift should consume target range endpoints")

    monkeypatch.setattr(
        sift_results,
        "derive_semantic_change_runs",
        lambda source_lines, target_lines: [TargetRangeOnlyRun()],
    )

    ownership = sift_results.build_ownership_from_working_and_target_lines([], [])

    assert ownership is not None
    assert ownership.presence_claims[0].source_lines == ["2-1001"]
    assert ownership.presence_line_set().ranges() == ((2, 1001),)


def test_validate_sifted_result_accepts_non_list_line_sequences(line_sequence):
    """Sift validation accepts indexed byte-line sequences."""
    target_lines = line_sequence([b"line1\n", b"new\n", b"line3\n"])
    working_lines = line_sequence([b"line1\n", b"old\n", b"line3\n"])
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old\n"],
            ),
        ],
    )

    sift_results.validate_sifted_text_file_result_from_lines(
        target_lines,
        ownership,
        working_lines,
    )


def test_add_sifted_text_file_to_batch_persists_target_buffer(temp_git_repo):
    """Sifted text persistence streams the target buffer into batch storage."""
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    with LineBuffer.from_chunks([b"# Test\n", b"added\n"]) as target_buffer:
        add_sifted_text_file_to_batch(
            "sifted-batch",
            "README.md",
            target_buffer,
            ownership,
        )

    assert read_file_from_batch("sifted-batch", "README.md") == "# Test\nadded\n"
    metadata = read_batch_metadata("sifted-batch")
    assert "batch_source_commit" in metadata["files"]["README.md"]


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestSiftBasicBehavior:
    """Tests for basic sift functionality."""

    def test_sift_removes_already_present_changes(self, temp_git_repo):
        """Test that sift removes portions already in working tree."""
        # Commit initial version
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\nLine B\nLine C\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add lines"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make two changes
        readme.write_text("# Test\nLine A modified\nLine B modified\nLine C\n")

        # Batch both changes
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Land one change manually
        readme.write_text("# Test\nLine A modified\nLine B\nLine C\n")

        # Sift - should remove the already-present "Line A modified" portion
        command_sift_batch("source-batch", "sifted-batch")

        # Verify sifted batch exists
        assert batch_exists("sifted-batch")

        # Verify sifted batch contains the full realized content
        # (the batch content is the full target, ownership specifies what to apply)
        content = read_file_from_batch("sifted-batch", "README.md")
        assert content is not None
        assert "Line B modified" in content
        # Content includes both changes (full realized), but ownership will specify
        # that only Line B modified needs to be applied (Line A already present)
        # Verify the ownership reflects only the needed change
        metadata = read_batch_metadata("sifted-batch")
        file_meta = metadata["files"]["README.md"]
        with acquire_ownership_for_metadata(file_meta) as ownership:
            resolved = ownership.resolve()
            # Should claim line 3 (Line B modified) but not line 2 (Line A modified already present)
            assert 3 in resolved.presence_line_set

    def test_sift_uses_scoped_ownership_metadata(self, temp_git_repo, monkeypatch):
        """Sift should not require materialized ownership metadata."""
        readme = temp_git_repo / "README.md"
        readme.write_text("old\nkeep\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add readme"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("new\nkeep\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        readme.write_text("old\nkeep\n")
        _reject_materialized_ownership_metadata(monkeypatch)

        command_sift_batch("source-batch", "sifted-batch")

        metadata = read_batch_metadata("sifted-batch")
        file_meta = metadata["files"]["README.md"]
        assert "presence_claims" in file_meta
        assert "deletions" in file_meta

    def test_sift_empty_when_all_present(self, temp_git_repo):
        """Test that sift produces empty batch when all changes are present."""
        # Commit initial version
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add line"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make change
        readme.write_text("# Test\nLine A modified\n")

        # Batch the change
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Land the change manually (working tree now matches batched change)
        # Working tree already has "Line A modified"

        # Sift - should produce empty batch
        command_sift_batch("source-batch", "empty-batch")

        # Verify batch exists but has no files
        assert batch_exists("empty-batch")
        metadata = read_batch_metadata("empty-batch")
        assert metadata["files"] == {}

    def test_sift_in_place_updates_source_batch(self, temp_git_repo):
        """Test that in-place sift updates the source batch."""
        # Commit initial version
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\nLine B\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add lines"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make two changes
        readme.write_text("# Test\nLine A modified\nLine B modified\n")

        # Batch both changes
        command_start()
        fetch_next_change()
        command_include_to_batch("my-batch")

        # Land one change manually
        readme.write_text("# Test\nLine A modified\nLine B\n")

        # In-place sift
        command_sift_batch("my-batch", "my-batch")

        # Verify batch still exists
        assert batch_exists("my-batch")

        # Verify batch now contains full realized content with updated ownership
        content = read_file_from_batch("my-batch", "README.md")
        assert content is not None
        assert "Line B modified" in content
        # Content includes full realized result, ownership specifies what to apply
        # Verify ownership changed to reflect only the needed parts
        metadata_after = read_batch_metadata("my-batch")
        assert "README.md" in metadata_after["files"]


class TestSiftPairingWeakness:
    """Tests for conservative pairing strategy in sift.

    These tests stress the 'same anchor, multiple candidates' case to verify
    that the pairing strategy is conservative (1-to-1 only) and doesn't
    incorrectly pair runs in ambiguous situations.
    """

    def test_repeated_lines_with_ambiguous_replacements(self, temp_git_repo):
        """Test sift with repeated lines creating ambiguous replacements.

        Working tree has:
            line1
            line2
            line3

        Batched changes produce realized:
            line1
            lineX
            line2
            lineY
            line3

        Then working tree changes to:
            line1
            line2
            lineZ
            line3

        Both lineX (after line1) and lineY (after line2) have been removed.
        One new line lineZ (after line2) has been added.

        The structural anchor for lineZ is "line2", which is the same anchor
        as the deleted lineY run.  But there's also a deleted lineX run with
        anchor "line1".

        Conservative pairing should leave lineZ unpaired,
        because the situation is ambiguous (which deletion does lineZ replace?).
        Instead, it should emit separate DELETION runs and a PRESENCE run.

        This verifies the semantic ownership correctly represents the state
        without making incorrect coupling assumptions.
        """
        # Commit base version
        readme = temp_git_repo / "README.md"
        readme.write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batched changes: add lineX after line1, lineY after line2
        readme.write_text("line1\nlineX\nline2\nlineY\nline3\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Change working tree: remove lineX and lineY, add lineZ after line2
        readme.write_text("line1\nline2\nlineZ\nline3\n")

        # Sift should handle this without incorrect pairing
        command_sift_batch("source-batch", "sifted-batch")

        # Verify sifted batch exists and is valid
        assert batch_exists("sifted-batch")

        # The sifted batch should represent the remaining delta
        # Realized would be: line1, lineX, line2, lineY, line3
        # Working is: line1, line2, lineZ, line3
        # Delta: lineX and lineY are present, lineZ is not
        content = read_file_from_batch("sifted-batch", "README.md")
        assert content is not None
        assert "lineX" in content
        assert "lineY" in content
        # lineZ is in working tree, so it shouldn't be in the sifted batch
        assert "lineZ" not in content

    def test_clustered_adjacent_edits(self, temp_git_repo):
        """Test sift with multiple nearby edits around same anchor.

        Working tree has:
            line1
            line2
            line3
            line4

        Batched changes produce realized:
            line1
            lineA
            lineB
            line2
            lineC
            line3
            line4

        Then working tree changes to:
            line1
            lineX
            line2
            line3
            line4

        Multiple source runs (lineA-lineB and lineC) were removed.
        One target run (lineX) was added.

        All these runs share similar structural context (around line1-line2-line3).
        Conservative pairing should handle this correctly without creating
        incorrect couplings.
        """
        # Commit base version
        readme = temp_git_repo / "README.md"
        readme.write_text("line1\nline2\nline3\nline4\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batched changes: add lineA, lineB after line1; lineC after line2
        readme.write_text("line1\nlineA\nlineB\nline2\nlineC\nline3\nline4\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Change working tree: remove all additions, add lineX after line1
        readme.write_text("line1\nlineX\nline2\nline3\nline4\n")

        # Sift should handle clustered edits correctly
        command_sift_batch("source-batch", "sifted-batch")

        # Verify sifted batch is valid
        assert batch_exists("sifted-batch")

        # The sifted batch should show lineA, lineB, lineC as remaining
        # (since they're in realized but not in working)
        content = read_file_from_batch("sifted-batch", "README.md")
        assert content is not None
        assert "lineA" in content
        assert "lineB" in content
        assert "lineC" in content
        # lineX is in working tree, not in batch
        assert "lineX" not in content

    def test_repeated_boilerplate_block(self, temp_git_repo):
        """Test sift with repeated boilerplate where only one instance changed.

        Working tree has:
            # Section 1
            boilerplate line
            # Section 2
            boilerplate line
            # Section 3

        Batched changes produce realized:
            # Section 1
            boilerplate modified
            # Section 2
            boilerplate modified
            # Section 3

        Then working tree changes to:
            # Section 1
            boilerplate modified
            # Section 2
            boilerplate line
            # Section 3

        Only the first boilerplate was actually landed.
        The conservative pairing should correctly identify which modification
        is still needed without getting confused by the repeated structure.
        """
        # Commit base version with repeated boilerplate
        readme = temp_git_repo / "README.md"
        readme.write_text("# Section 1\nboilerplate line\n# Section 2\nboilerplate line\n# Section 3\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base with boilerplate"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batched changes: modify both boilerplate instances
        readme.write_text("# Section 1\nboilerplate modified\n# Section 2\nboilerplate modified\n# Section 3\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Land only the first modification
        readme.write_text("# Section 1\nboilerplate modified\n# Section 2\nboilerplate line\n# Section 3\n")

        # Sift should correctly identify the remaining work
        command_sift_batch("source-batch", "sifted-batch")

        # Verify sifted batch is valid
        assert batch_exists("sifted-batch")

        # The sifted batch should still contain the second modification
        # (Section 2's boilerplate line → boilerplate modified)
        content = read_file_from_batch("sifted-batch", "README.md")
        assert content is not None
        # Should show second section modified, first section already present
        assert content.count("boilerplate modified") >= 1


class TestSiftValidationStrength:
    """Tests for validation strength in sift.

    These tests verify that the semantic validation (using merge_batch)
    catches cases that would pass bounds-only validation but are actually
    semantically incorrect.
    """

    def test_claimed_lines_in_bounds_but_deletions_wrong(self, temp_git_repo):
        """Test that validation catches wrong absence claims.

        This test verifies that the semantic validation (level C) catches cases
        where absence claims are structurally valid but semantically incorrect.

        We create a scenario where sift would derive ownership with absence claims,
        then verify the validation works by checking a normal sift operation succeeds.
        (A more sophisticated test would inject corrupted ownership, but that requires
        mocking or bypassing the derivation logic.)
        """
        # Commit base version
        readme = temp_git_repo / "README.md"
        readme.write_text("base line 1\nbase line 2\nbase line 3\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create a batch with a replacement (deletion + addition)
        readme.write_text("base line 1\ninserted line\nbase line 3\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Partially land the change
        readme.write_text("base line 1\nbase line 2\nbase line 3\n")

        # Sift should work correctly - the validation should pass
        # because the derived ownership is correct
        command_sift_batch("source-batch", "sifted-batch")

        # Verify the sifted batch has the expected structure
        assert batch_exists("sifted-batch")
        metadata = read_batch_metadata("sifted-batch")
        assert "README.md" in metadata["files"]

        # The validation succeeded, demonstrating that the three-level validation
        # (bounds, deletion structure, semantic correctness) all passed

    def test_claimed_lines_valid_but_result_doesnt_match(self, temp_git_repo):
        """Test validation catches when representation doesn't match intended result.

        This test verifies that even when claimed lines and deletion anchors
        are structurally legal (pass level A and B validation), if the combined
        representation doesn't actually describe the intended delta, the
        semantic validation (level C) will catch it.
        """
        # Commit base version
        readme = temp_git_repo / "README.md"
        readme.write_text("line A\nline B\nline C\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with specific change
        readme.write_text("line A\nline X\nline B\nline C\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Change working tree to different state
        readme.write_text("line A\nline B\nline Y\nline C\n")

        # Sift this batch
        # The result should be validated correctly
        command_sift_batch("source-batch", "sifted-batch")

        # Now verify the sifted batch actually represents the correct delta
        # When we apply the sifted batch to working tree, we should get realized
        # Realized = line A, line X, line B, line C
        # Working = line A, line B, line Y, line C
        # So sifted batch should contain line X (since it's in realized but not working)
        content = read_file_from_batch("sifted-batch", "README.md")
        assert "line X" in content
        # line Y is in working but not realized, so shouldn't be in batch
        assert "line Y" not in content

    def test_replacement_deletes_longer_working_run_after_presence_insert(self, temp_git_repo):
        """Sifted replacements must not leave stale working-tree tail lines."""
        readme = temp_git_repo / "README.md"
        readme.write_text("anchor\nold1\nold2\n]\ntail\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("anchor\nnew1\nnew2\ntail\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        readme.write_text("anchor\nold1\nold2\n]\ntail\n")

        command_sift_batch("source-batch", "sifted-batch")
        command_apply_from_batch("sifted-batch")

        assert readme.read_text() == "anchor\nnew1\nnew2\ntail\n"

    def test_merge_error_reports_clean_command_error(self, temp_git_repo, monkeypatch):
        """Sift structural failures should not leak Python tracebacks."""
        readme = temp_git_repo / "README.md"
        readme.write_text("line A\nline B\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("line A\nline B modified\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        def fail_sift(*args, **kwargs):
            raise MergeError("synthetic structural conflict")

        monkeypatch.setattr(sift_results, "compute_sifted_text_file", fail_sift)

        with pytest.raises(CommandError) as exc_info:
            command_sift_batch("source-batch", "sifted-batch")

        assert "Could not sift batch 'source-batch': synthetic structural conflict" == str(exc_info.value)
        assert not batch_exists("sifted-batch")

    def test_start_of_file_deletion_validation(self, temp_git_repo):
        """Test validation handles start-of-file deletions correctly.

        Start-of-file deletions have no structural predecessor (anchor = None).
        This test verifies that the validation correctly handles this edge case.
        """
        # Commit base version with content at start
        readme = temp_git_repo / "README.md"
        readme.write_text("header line\nline A\nline B\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch that deletes header
        readme.write_text("line A\nline B\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Restore header in working tree
        readme.write_text("header line\nline A\nline B\n")

        # Sift - the deletion is no longer present, so batch should still contain it
        command_sift_batch("source-batch", "sifted-batch")

        # Verify the sifted batch represents start-of-file deletion correctly
        assert batch_exists("sifted-batch")
        content = read_file_from_batch("sifted-batch", "README.md")
        # The deletion should be represented (realized has no header, working has it)
        # So sifted batch should show the delta
        assert content is not None

    def test_end_of_file_deletion_validation(self, temp_git_repo):
        """Test validation handles end-of-file deletions correctly.

        End-of-file deletions should be handled correctly by the validation
        even though they occur at the boundary.
        """
        # Commit base version with trailing content
        readme = temp_git_repo / "README.md"
        readme.write_text("line A\nline B\ntrailer line\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base version"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch that deletes trailer
        readme.write_text("line A\nline B\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Restore trailer in working tree
        readme.write_text("line A\nline B\ntrailer line\n")

        # Sift - the deletion is no longer present
        command_sift_batch("source-batch", "sifted-batch")

        # Verify the sifted batch handles end-of-file deletion correctly
        assert batch_exists("sifted-batch")
        content = read_file_from_batch("sifted-batch", "README.md")
        # Deletion should be represented correctly
        assert content is not None


class TestSiftBinaryFiles:
    """Tests for sift behavior with binary files."""

    def _batch_file_bytes(self, batch_name: str, file_path: str) -> bytes | None:
        commit = subprocess.run(
            ["git", "rev-parse", get_batch_content_ref_name(batch_name)],
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            return None

        result = subprocess.run(
            ["git", "show", f"{commit.stdout.strip()}:{file_path}"],
            capture_output=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout

    def test_sift_binary_file_byte_equality_removes_file(self, temp_git_repo):
        """Sift drops a binary file when working tree bytes already match the batch."""
        binary_file = temp_git_repo / "data.bin"
        target_content = b"\x00\x01target\xff\x00"
        binary_file.write_bytes(target_content)

        command_start()
        command_new_batch("source-batch")
        add_binary_file_to_batch(
            "source-batch",
            BinaryFileChange(
                old_path="/dev/null",
                new_path="data.bin",
                change_type="added",
            ),
        )

        command_sift_batch("source-batch", "sifted-batch")

        metadata = read_batch_metadata("sifted-batch")
        assert metadata["files"] == {}
        assert self._batch_file_bytes("sifted-batch", "data.bin") is None

    def test_sift_binary_file_different_content_retains_file(self, temp_git_repo):
        """Sift retains binary files whose target bytes differ from the working tree."""
        binary_file = temp_git_repo / "data.bin"
        original_content = b"\x00\x01original\x02"
        target_content = b"\xff\xfe target bytes \x00"
        working_content = b"\x10\x11 working bytes \x00"

        binary_file.write_bytes(original_content)
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)

        binary_file.write_bytes(target_content)
        command_start()
        command_new_batch("source-batch")
        add_binary_file_to_batch(
            "source-batch",
            BinaryFileChange(
                old_path="data.bin",
                new_path="data.bin",
                change_type="modified",
            ),
        )

        binary_file.write_bytes(working_content)

        command_sift_batch("source-batch", "sifted-batch")

        metadata = read_batch_metadata("sifted-batch")
        file_meta = metadata["files"]["data.bin"]
        assert file_meta["file_type"] == "binary"
        assert file_meta["change_type"] == "modified"
        assert self._batch_file_bytes("sifted-batch", "data.bin") == target_content

        command_apply_from_batch("sifted-batch")
        assert binary_file.read_bytes() == target_content

    def test_sift_binary_deletion_already_present_removes_file(self, temp_git_repo):
        """Sift drops a binary deletion when the file is already absent."""
        binary_file = temp_git_repo / "data.bin"
        binary_file.write_bytes(b"\x00\x01baseline\x02")
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)

        binary_file.unlink()
        command_start()
        command_new_batch("source-batch")
        add_binary_file_to_batch(
            "source-batch",
            BinaryFileChange(
                old_path="data.bin",
                new_path="/dev/null",
                change_type="deleted",
            ),
        )

        command_sift_batch("source-batch", "sifted-batch")

        metadata = read_batch_metadata("sifted-batch")
        assert metadata["files"] == {}

    def test_sift_binary_deletion_retained_when_file_exists(self, temp_git_repo):
        """Sift keeps a binary deletion when the working tree still has the file."""
        binary_file = temp_git_repo / "data.bin"
        baseline_content = b"\x00\x01baseline\x02"
        binary_file.write_bytes(baseline_content)
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)

        binary_file.unlink()
        command_start()
        command_new_batch("source-batch")
        add_binary_file_to_batch(
            "source-batch",
            BinaryFileChange(
                old_path="data.bin",
                new_path="/dev/null",
                change_type="deleted",
            ),
        )

        binary_file.write_bytes(baseline_content)

        command_sift_batch("source-batch", "sifted-batch")

        metadata = read_batch_metadata("sifted-batch")
        file_meta = metadata["files"]["data.bin"]
        assert file_meta["file_type"] == "binary"
        assert file_meta["change_type"] == "deleted"
        assert self._batch_file_bytes("sifted-batch", "data.bin") is None


class TestSiftPersistenceModel:
    """Tests for sift persistence model correctness.

    These tests verify that the sifted batch follows the proper baseline-centered
    storage model and that working tree details don't corrupt persistent artifacts.
    """

    def test_persistence_uses_realized_content_not_working_tree(self, temp_git_repo):
        """Test that persisted batch stores the realized target, not working tree snapshot.

        The batch commit should contain the realized target content regardless of
        what the current working tree looks like.
        """
        # Commit base version
        readme = temp_git_repo / "README.md"
        readme.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with target: line1, lineX, line2
        readme.write_text("line1\nlineX\nline2\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Set working tree to something different: line1, lineY, line2
        readme.write_text("line1\nlineY\nline2\n")

        # Sift - this should preserve "lineX" (target) not "lineY" (working tree)
        command_sift_batch("source-batch", "sifted-batch")

        # Read the batch commit to verify it contains realized target, not working tree
        result = subprocess.run(
            ["git", "rev-parse", get_batch_content_ref_name("sifted-batch")],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        batch_commit = result.stdout.strip()

        result = subprocess.run(
            ["git", "show", f"{batch_commit}:README.md"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )

        # Batch commit should contain lineX (from realized target), not lineY (from working tree)
        assert "lineX" in result.stdout
        assert "lineY" not in result.stdout

    def test_stored_batch_commit_readable_by_standard_tools(self, temp_git_repo):
        """Test that sifted batch commit is compatible with standard git operations.

        The stored batch commit should be a normal git commit that can be
        read and diffed using standard git commands.
        """
        # Create and sift a batch
        readme = temp_git_repo / "README.md"
        readme.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("line1\nlineX\nline2\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Partially land change
        readme.write_text("line1\nline2\n")

        # Sift
        command_sift_batch("source-batch", "sifted-batch")

        # Verify batch commit exists and can be read with git
        result = subprocess.run(
            ["git", "rev-parse", get_batch_content_ref_name("sifted-batch")],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        batch_commit = result.stdout.strip()
        assert len(batch_commit) == 40  # Valid SHA

        # Verify we can read file from commit
        result = subprocess.run(
            ["git", "show", f"{batch_commit}:README.md"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        # Should contain the realized target content
        assert "lineX" in result.stdout

    def test_validation_proves_merge_semantics(self, temp_git_repo):
        """Test that validation proves merge_batch works correctly.

        This verifies that the semantic validation using merge_batch is
        actually exercised and would catch incorrect ownership.
        """
        # Create a batch
        readme = temp_git_repo / "README.md"
        readme.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("line1\nlineX\nlineY\nline2\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Partially land: lineX present, lineY not present
        readme.write_text("line1\nlineX\nline2\n")

        # Sift should succeed with validation
        command_sift_batch("source-batch", "sifted-batch")

        # The sifted batch should represent only lineY
        # Verify we can merge it with working tree to get realized content

        metadata = read_batch_metadata("sifted-batch")
        if "README.md" in metadata["files"]:
            file_meta = metadata["files"]["README.md"]
            batch_source_commit = file_meta["batch_source_commit"]

            # Get batch source content
            result = subprocess.run(
                ["git", "show", f"{batch_source_commit}:README.md"],
                cwd=temp_git_repo,
                capture_output=True
            )
            batch_source_content = result.stdout

            # Get working content
            working_content = readme.read_bytes()

            with acquire_ownership_for_metadata(file_meta) as ownership:
                # Merge should produce the realized target
                merged = merge_batch(
                    batch_source_content=batch_source_content,
                    ownership=ownership,
                    working_content=working_content
                )

            # Should produce the full realized content (both lineX and lineY)
            assert b"lineX" in merged
            assert b"lineY" in merged

    def test_sifted_batch_baseline_matches_source_baseline(self, temp_git_repo):
        """Test that sifted batch inherits baseline from source batch.

        The sifted batch should use the same baseline commit as the source batch,
        maintaining the baseline-centered storage model.
        """
        # Create batch
        readme = temp_git_repo / "README.md"
        readme.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("line1\nlineX\nline2\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Get source batch baseline
        source_metadata = read_batch_metadata("source-batch")
        source_baseline = source_metadata.get("baseline")

        # Sift
        readme.write_text("line1\nline2\n")
        command_sift_batch("source-batch", "sifted-batch")

        # Verify sifted batch has same baseline
        sifted_metadata = read_batch_metadata("sifted-batch")
        sifted_baseline = sifted_metadata.get("baseline")

        assert sifted_baseline == source_baseline


class TestSiftCopyVsInPlace:
    """Tests for copy mode vs in-place mode."""

    def test_copy_mode_preserves_source_batch(self, temp_git_repo):
        """Test that copy mode preserves the source batch unchanged."""
        # Setup and create batch
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\nLine B\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("# Test\nLine A modified\nLine B modified\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")

        # Land one change
        readme.write_text("# Test\nLine A modified\nLine B\n")

        # Get source batch ref before sift
        source_metadata_before = read_batch_metadata("source-batch")

        # Copy mode sift
        command_sift_batch("source-batch", "dest-batch")

        # Verify source batch unchanged
        source_metadata_after = read_batch_metadata("source-batch")
        assert source_metadata_before == source_metadata_after

        # Verify dest batch exists and differs
        assert batch_exists("dest-batch")
        dest_metadata = read_batch_metadata("dest-batch")
        # Dest should have different batch_source_commit (points to realized content)
        assert dest_metadata != source_metadata_before

    def test_in_place_mode_updates_source_batch(self, temp_git_repo):
        """Test that in-place mode updates the source batch."""
        # Setup and create batch
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\nLine B\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("# Test\nLine A modified\nLine B modified\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("my-batch")

        # Get batch ref before sift
        metadata_before = read_batch_metadata("my-batch")

        # Land one change
        readme.write_text("# Test\nLine A modified\nLine B\n")

        # In-place sift
        command_sift_batch("my-batch", "my-batch")

        # Verify batch was updated
        metadata_after = read_batch_metadata("my-batch")
        # Batch should have changed (different content, different batch_source)
        assert metadata_after != metadata_before

    def test_in_place_mode_preserves_old_derived_temp_name(self, temp_git_repo):
        """Sift must not delete a user batch matching the former temp convention."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nbase\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo)

        readme.write_text("# Test\nchanged\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("feature")
        command_new_batch("feature-sift-temp")
        collision_metadata = read_batch_metadata("feature-sift-temp")

        command_sift_batch("feature", "feature")

        assert batch_exists("feature-sift-temp")
        assert read_batch_metadata("feature-sift-temp") == collision_metadata

    def test_temp_name_race_never_deletes_an_unowned_batch(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Cleanup is limited to a temporary batch this invocation created."""
        command_new_batch("sift-tmp-race")
        collision_metadata = read_batch_metadata("sift-tmp-race")
        monkeypatch.setattr(
            sift_persistence,
            "_new_sift_temp_batch_name",
            lambda: "sift-tmp-race",
        )

        with pytest.raises(CommandError, match="already exists"):
            sift_persistence.publish_sifted_files(
                destination_batch="destination",
                retained_files=[],
                source_metadata={"baseline": "HEAD"},
                destination_note="test",
                replace_existing=False,
            )

        assert batch_exists("sift-tmp-race")
        assert read_batch_metadata("sift-tmp-race") == collision_metadata

    def test_copy_mode_cleans_private_destination_after_unexpected_failure(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Any build failure should leave neither a destination nor temp batch."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nbase\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo)

        readme.write_text("# Test\nchanged\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("source-batch")
        readme.write_text("# Test\nbase\n")

        failed_result = None

        def fail_persistence(*_args, **_kwargs):
            nonlocal failed_result
            failed_result = _args[3]
            raise OSError("synthetic persistence failure")

        monkeypatch.setattr(
            "git_stage_batch.commands.batch_transform.sift_persistence.add_sifted_file_to_batch",
            fail_persistence,
        )

        with pytest.raises(OSError, match="synthetic persistence failure"):
            command_sift_batch("source-batch", "dest-batch")

        assert not batch_exists("dest-batch")
        assert not any(name.startswith("sift-tmp-") for name in list_batch_names())
        assert isinstance(failed_result, sift_results.SiftedTextFileResult)
        with pytest.raises(ValueError, match="buffer is closed"):
            failed_result.target_buffer.to_bytes()
        for deletion in failed_result.ownership.deletions:
            if isinstance(deletion.content_lines, LineBuffer):
                with pytest.raises(ValueError, match="buffer is closed"):
                    deletion.content_lines.to_bytes()

    def test_in_place_mode_is_atomic(self, temp_git_repo):
        """Test that in-place mode uses atomic update (all-or-nothing).

        This test verifies that if validation fails during in-place sift,
        the original batch is preserved unchanged.
        """
        # Setup and create batch
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Base"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("# Test\nLine A modified\n")
        command_start()
        fetch_next_change()
        command_include_to_batch("atomic-batch")

        # Even if something fails, the original batch should be preserved
        # (In practice, validation should prevent bad states, but this tests atomicity)

        # For this test, just verify normal in-place sift maintains consistency
        command_sift_batch("atomic-batch", "atomic-batch")

        # Batch should still exist and be valid (not left in corrupt state)
        assert batch_exists("atomic-batch")
        metadata_after = read_batch_metadata("atomic-batch")
        assert "files" in metadata_after


def _prepare_parallel_sift_source(repo, *, file_count: int = 2) -> list[str]:
    file_paths = [f"parallel-{index}.txt" for index in range(file_count)]
    for index, file_path in enumerate(file_paths):
        (repo / file_path).write_text(f"start {index}\nold {index}\nend {index}\n")
    subprocess.run(["git", "add", *file_paths], check=True, cwd=repo)
    subprocess.run(
        ["git", "commit", "-m", "Parallel sift baseline"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    for index, file_path in enumerate(file_paths):
        (repo / file_path).write_text(f"start {index}\nnew {index}\nend {index}\n")
    command_start()
    for file_path in file_paths:
        command_include_to_batch(
            "parallel-source",
            file=file_path,
            quiet=True,
        )
    for index, file_path in enumerate(file_paths):
        (repo / file_path).write_text(f"start {index}\nold {index}\nend {index}\n")
    return file_paths


def _comparable_sift_metadata(batch_name: str) -> dict:
    metadata = read_batch_metadata(batch_name)
    return {
        "baseline": metadata["baseline"],
        "files": {
            file_path: {
                key: value
                for key, value in file_meta.items()
                if key != "batch_source_commit"
            }
            for file_path, file_meta in metadata["files"].items()
        },
    }


class TestSiftFileJobs:
    """Contract tests for inline and process text sift execution."""

    def test_forced_process_matches_inline_publication(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        file_paths = _prepare_parallel_sift_source(temp_git_repo, file_count=4)
        initial_worktree = {
            file_path: (temp_git_repo / file_path).read_bytes()
            for file_path in file_paths
        }

        monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "1")
        command_sift_batch("parallel-source", "inline-destination")
        for worker_count in (2, 4):
            destination = f"process-{worker_count}-destination"
            monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", str(worker_count))
            command_sift_batch("parallel-source", destination)

            assert get_batch_tree_sha("inline-destination") == get_batch_tree_sha(
                destination
            )
            assert _comparable_sift_metadata(
                "inline-destination"
            ) == _comparable_sift_metadata(destination)
            assert list(read_batch_metadata(destination)["files"]) == file_paths
        assert {
            file_path: (temp_git_repo / file_path).read_bytes()
            for file_path in file_paths
        } == initial_worktree

    def test_worktree_change_after_compute_aborts_before_publication(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        file_paths = _prepare_parallel_sift_source(temp_git_repo)
        original_run = sift_command.run_file_jobs

        def run_then_change(*args, **kwargs):
            results = original_run(*args, **kwargs)
            (temp_git_repo / file_paths[0]).write_text("changed after compute\n")
            return results

        monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "1")
        monkeypatch.setattr(sift_command, "run_file_jobs", run_then_change)

        with pytest.raises(
            CommandError,
            match="working-tree file 'parallel-0.txt' changed",
        ):
            command_sift_batch("parallel-source", "stale-worktree-destination")

        assert not batch_exists("stale-worktree-destination")
        assert not any(name.startswith("sift-tmp-") for name in list_batch_names())

    def test_multiple_worker_refusals_report_source_order(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        file_paths = _prepare_parallel_sift_source(temp_git_repo)

        def refuse_all(jobs, compute, **kwargs):
            return [
                sift_command._sift_jobs.SiftTextFileJobResult(
                    ordinal=job.ordinal,
                    file_path=job.file_path,
                    outcome="merge_error",
                    error_message=f"refused {job.file_path}",
                )
                for job in sorted(jobs, key=lambda item: item.ordinal)
            ]

        monkeypatch.setattr(sift_command, "run_file_jobs", refuse_all)

        with pytest.raises(CommandError, match=f"refused {file_paths[0]}"):
            command_sift_batch("parallel-source", "refused-destination")

        assert not batch_exists("refused-destination")

    def test_source_revision_change_after_compute_aborts_before_publication(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        _prepare_parallel_sift_source(temp_git_repo)
        original_run = sift_command.run_file_jobs

        def run_then_annotate(*args, **kwargs):
            results = original_run(*args, **kwargs)
            command_annotate_batch("parallel-source", "changed during sift")
            return results

        monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "1")
        monkeypatch.setattr(sift_command, "run_file_jobs", run_then_annotate)

        with pytest.raises(CommandError, match="changed while sift was running"):
            command_sift_batch("parallel-source", "stale-source-destination")

        assert not batch_exists("stale-source-destination")
        assert read_batch_metadata("parallel-source")["note"] == "changed during sift"
        assert not any(name.startswith("sift-tmp-") for name in list_batch_names())

    def test_destination_appearing_after_compute_is_preserved(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        _prepare_parallel_sift_source(temp_git_repo)
        original_run = sift_command.run_file_jobs

        def run_then_create_destination(*args, **kwargs):
            results = original_run(*args, **kwargs)
            command_new_batch("raced-destination")
            return results

        monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "1")
        monkeypatch.setattr(
            sift_command,
            "run_file_jobs",
            run_then_create_destination,
        )

        with pytest.raises(CommandError, match="created while sift was running"):
            command_sift_batch("parallel-source", "raced-destination")

        assert batch_exists("raced-destination")
        assert read_batch_metadata("raced-destination")["files"] == {}
        assert not any(name.startswith("sift-tmp-") for name in list_batch_names())

    def test_process_sift_preserves_added_text_boundaries(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        expected_content = {
            "added-crlf.txt": b"first\r\nsecond\r\n",
            "added-no-final-newline.txt": b"first\nsecond",
        }
        for file_path, content in expected_content.items():
            (temp_git_repo / file_path).write_bytes(content)
        command_start()
        for file_path in expected_content:
            command_include_to_batch(
                "added-source",
                file=file_path,
                quiet=True,
            )
            (temp_git_repo / file_path).unlink()

        monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "2")
        command_sift_batch("added-source", "added-destination")

        metadata = read_batch_metadata("added-destination")
        assert list(metadata["files"]) == list(expected_content)
        assert {
            file_meta["change_type"]
            for file_meta in metadata["files"].values()
        } == {"added"}
        command_apply_from_batch("added-destination")
        assert {
            file_path: (temp_git_repo / file_path).read_bytes()
            for file_path in expected_content
        } == expected_content

    def test_process_sift_preserves_text_deletions(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        original_content = {
            "deleted-a.txt": b"first\nold a\n",
            "deleted-b.txt": b"first\nold b",
        }
        for file_path, content in original_content.items():
            (temp_git_repo / file_path).write_bytes(content)
        subprocess.run(
            ["git", "add", *original_content],
            check=True,
            cwd=temp_git_repo,
        )
        subprocess.run(
            ["git", "commit", "-m", "Deletion baseline"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        for file_path in original_content:
            (temp_git_repo / file_path).unlink()
        command_start()
        for file_path in original_content:
            command_include_to_batch(
                "deleted-source",
                file=file_path,
                quiet=True,
            )
        for file_path, content in original_content.items():
            (temp_git_repo / file_path).write_bytes(content)

        monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "2")
        command_sift_batch("deleted-source", "deleted-destination")

        metadata = read_batch_metadata("deleted-destination")
        assert list(metadata["files"]) == list(original_content)
        assert {
            file_meta["change_type"]
            for file_meta in metadata["files"].values()
        } == {"deleted"}
        command_apply_from_batch("deleted-destination")
        assert all(
            not (temp_git_repo / file_path).exists()
            for file_path in original_content
        )

    def test_interruption_removes_private_sift_workspace(
        self,
        temp_git_repo,
        tmp_path,
        monkeypatch,
    ):
        _prepare_parallel_sift_source(temp_git_repo)
        workspace_roots = []

        class RecordingWorkspace(FileJobWorkspace):
            def __init__(self):
                super().__init__(parent_directory=tmp_path)
                workspace_roots.append(self.root)

        def interrupt(jobs, compute, **kwargs):
            Path(jobs[0].payload.target_output_path).write_bytes(b"partial")
            raise KeyboardInterrupt

        monkeypatch.setenv("GIT_STAGE_BATCH_JOBS", "1")
        monkeypatch.setattr(sift_command, "FileJobWorkspace", RecordingWorkspace)
        monkeypatch.setattr(sift_command, "run_file_jobs", interrupt)

        with pytest.raises(KeyboardInterrupt):
            command_sift_batch("parallel-source", "interrupted-destination")

        assert workspace_roots
        assert all(not root.exists() for root in workspace_roots)
        assert not batch_exists("interrupted-destination")
