"""Tests for binary diff helper functions."""

from __future__ import annotations

from git_stage_batch.core import binary_diff


def test_binary_metadata_detection_reads_binary_file_line():
    """Binary-file metadata should identify binary diffs."""
    assert binary_diff.metadata_indicates_binary_file(
        [b"Binary files a/image.png and b/image.png differ"]
    )
    assert binary_diff.binary_file_diff_line(
        [b"index 1111111..2222222", b"Binary files a/a.bin and b/a.bin differ"]
    ) == b"Binary files a/a.bin and b/a.bin differ"
    assert not binary_diff.metadata_indicates_binary_file(
        [b"index 1111111..2222222 100644"]
    )
    assert binary_diff.binary_file_diff_line([]) is None


def test_binary_change_type_uses_dev_null_side():
    """Binary diff paths should produce added, deleted, or modified types."""
    assert (
        binary_diff.binary_change_type(
            [b"Binary files /dev/null and b/new_image.jpg differ"]
        )
        == "added"
    )
    assert (
        binary_diff.binary_change_type(
            [b"Binary files a/old_image.png and /dev/null differ"]
        )
        == "deleted"
    )
    assert (
        binary_diff.binary_change_type(
            [b"Binary files a/image.png and b/image.png differ"]
        )
        == "modified"
    )
    assert binary_diff.binary_change_type([]) == "modified"
