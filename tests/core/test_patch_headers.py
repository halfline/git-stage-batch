"""Tests for unified diff file header helper functions."""

from __future__ import annotations

from git_stage_batch.core import patch_headers


def test_patch_header_line_detection_matches_old_and_new_headers():
    """Patch file header helpers should recognize old and new header lines."""
    assert patch_headers.line_is_old_file_header(b"--- a/file.txt")
    assert patch_headers.line_is_new_file_header(b"+++ b/file.txt")
    assert not patch_headers.line_is_old_file_header(b"diff --git a/a b/a")
    assert not patch_headers.line_is_new_file_header(b"@@ -1 +1 @@")


def test_patch_header_path_helpers_normalize_git_prefixes():
    """Patch header path helpers should remove a/ and b/ prefixes."""
    assert patch_headers.old_file_path_from_header(b"--- a/src/file.txt") == (
        "src/file.txt"
    )
    assert patch_headers.new_file_path_from_header(b"+++ b/src/file.txt") == (
        "src/file.txt"
    )
    assert patch_headers.old_file_path_from_header(b"--- /dev/null") == "/dev/null"
    assert patch_headers.new_file_path_from_header(b"+++ /dev/null") == "/dev/null"


def test_line_change_path_prefers_non_null_new_path():
    """Line change paths should prefer the new side unless it is /dev/null."""
    assert patch_headers.line_change_path("old.txt", "new.txt") == "new.txt"
    assert patch_headers.line_change_path("old.txt", "/dev/null") == "old.txt"
    assert patch_headers.line_change_path("/dev/null", "new.txt") == "new.txt"
    assert patch_headers.line_change_path("", "") == ""


def test_patch_target_queries_read_dev_null_headers():
    """Patch target queries should detect new and deleted file paths."""
    assert patch_headers.patch_targets_new_file(
        [b"--- /dev/null\n", b"+++ b/new.txt\n"]
    )
    assert patch_headers.patch_targets_file_deletion(
        [b"--- a/old.txt\n", b"+++ /dev/null\n"]
    )
    assert not patch_headers.patch_targets_new_file(
        [b"--- a/file.txt\n", b"+++ b/file.txt\n"]
    )
    assert not patch_headers.patch_targets_file_deletion(
        [b"--- a/file.txt\n", b"+++ b/file.txt\n"]
    )
