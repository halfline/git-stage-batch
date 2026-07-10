"""Tests for unified diff header helper functions."""

from __future__ import annotations

from git_stage_batch.core import diff_headers


def test_line_is_diff_git_header_matches_git_file_headers():
    """Git file diff headers should be recognized by prefix."""
    assert diff_headers.line_is_diff_git_header(b"diff --git a/a.txt b/a.txt")
    assert not diff_headers.line_is_diff_git_header(b"--- a/a.txt")
    assert not diff_headers.line_is_diff_git_header(b"@@ -1 +1 @@")


def test_diff_git_paths_extracts_old_and_new_paths():
    """Old and new paths should be decoded from a git file diff header."""
    assert diff_headers.diff_git_paths(
        b"diff --git a/src/old.txt b/src/new.txt"
    ) == ("src/old.txt", "src/new.txt")


def test_diff_git_paths_returns_none_for_malformed_headers():
    """Malformed file diff headers should not yield paths."""
    assert diff_headers.diff_git_paths(b"--- a/src/old.txt") is None
    assert diff_headers.diff_git_paths(b"diff --git a/src/old.txt") is None
    assert diff_headers.diff_git_paths(b"diff --git b/src/new.txt") is None
