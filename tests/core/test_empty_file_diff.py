"""Tests for empty-file diff helper functions."""

from __future__ import annotations

from git_stage_batch.core import empty_file_diff


def test_new_empty_file_metadata_detection_uses_new_file_mode_without_digest():
    """A no-hunk new file is empty without inspecting its hash algorithm."""
    assert empty_file_diff.metadata_indicates_new_empty_file(
        [b"new file mode 100644", b"index 0000000..e69de29"]
    )
    assert empty_file_diff.metadata_indicates_new_empty_file(
        [b"new file mode 100644", b"index 0000000..1111111"]
    )
    assert not empty_file_diff.metadata_indicates_new_empty_file(
        [b"index 0000000..e69de29"]
    )


def test_synthetic_empty_file_patch_lines_add_terminators():
    """Synthetic empty-file patch lines should include diff terminators."""
    assert empty_file_diff.synthetic_empty_file_patch_lines(
        b"--- /dev/null",
        b"+++ b/empty.txt",
    ) == (
        b"--- /dev/null\n",
        b"+++ b/empty.txt\n",
        b"@@ -0,0 +0,0 @@\n",
    )


def test_synthetic_empty_file_patch_lines_preserve_existing_terminators():
    """Existing line terminators should not be duplicated."""
    assert empty_file_diff.synthetic_empty_file_patch_lines(
        b"--- /dev/null\n",
        b"+++ b/empty.txt\n",
    ) == (
        b"--- /dev/null\n",
        b"+++ b/empty.txt\n",
        b"@@ -0,0 +0,0 @@\n",
    )
