"""Tests for unified diff hunk header helper functions."""

from __future__ import annotations

import pytest

from git_stage_batch.core import hunk_headers
from git_stage_batch.exceptions import CommandError


def test_line_is_hunk_header_matches_unified_hunk_prefix():
    """Unified diff hunk headers should be recognized by prefix."""
    assert hunk_headers.line_is_hunk_header(b"@@ -1 +1 @@")
    assert not hunk_headers.line_is_hunk_header(b"--- a/file.txt")
    assert not hunk_headers.line_is_hunk_header(b"diff --git a/a b/a")


def test_parse_hunk_header_line_reads_explicit_lengths():
    """Hunk header parsing should read explicit old and new lengths."""
    header = hunk_headers.parse_hunk_header_line(b"@@ -10,3 +12,4 @@")

    assert header.old_start == 10
    assert header.old_len == 3
    assert header.new_start == 12
    assert header.new_len == 4


def test_parse_hunk_header_line_defaults_missing_lengths_to_one():
    """Hunk header parsing should default omitted lengths to one."""
    header = hunk_headers.parse_hunk_header_line(b"@@ -10 +12 @@")

    assert header.old_start == 10
    assert header.old_len == 1
    assert header.new_start == 12
    assert header.new_len == 1


def test_parse_hunk_header_line_rejects_malformed_header():
    """Malformed hunk headers should raise the parser command error."""
    with pytest.raises(CommandError, match="Bad hunk header"):
        hunk_headers.parse_hunk_header_line(b"@@ bad @@")
