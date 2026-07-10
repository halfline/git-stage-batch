"""Tests for generic file metadata diff helper functions."""

from __future__ import annotations

from git_stage_batch.core import file_metadata_diff


def test_metadata_indicates_rename_requires_from_and_to_markers():
    """Rename metadata should require both source and destination markers."""
    assert file_metadata_diff.metadata_indicates_rename(
        [b"rename from old.txt", b"rename to new.txt"]
    )
    assert not file_metadata_diff.metadata_indicates_rename(
        [b"rename from old.txt"]
    )
    assert not file_metadata_diff.metadata_indicates_rename(
        [b"rename to new.txt"]
    )


def test_metadata_indicates_deleted_file_reads_deleted_mode_marker():
    """Deleted-file metadata should be recognized by deleted mode marker."""
    assert file_metadata_diff.metadata_indicates_deleted_file(
        [b"deleted file mode 100644"]
    )
    assert not file_metadata_diff.metadata_indicates_deleted_file(
        [b"new file mode 100644"]
    )
