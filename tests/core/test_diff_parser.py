"""Tests for unified diff parser."""

import pytest

from git_stage_batch.core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
    patch_is_empty_file_change,
    patch_is_file_deletion,
    patch_is_new_file,
)
from git_stage_batch.core.models import SingleHunkPatch
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.exceptions import CommandError
from tests.diff_parser_helpers import collect_unified_diff


def _two_hunk_diff_lines() -> list[bytes]:
    diff = """\
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context
-old 1
+new 1
 context
@@ -10,3 +10,3 @@
 context
-old 2
+new 2
 context
"""
    return diff.encode("utf-8").splitlines(keepends=True)


class TestParseUnifiedDiff:
    """Tests for collect_unified_diff."""

    def test_single_file_single_hunk(self):
        """Test parsing a simple diff with one file and one hunk."""
        diff = """\
diff --git a/file.txt b/file.txt
index abc123..def456 100644
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context line
-old line
+new line
 another context
"""
        patches = list(collect_unified_diff(diff.encode('utf-8').splitlines(keepends=True)))

        assert len(patches) == 1
        patch = patches[0]
        assert patch.old_path == "file.txt"
        assert patch.new_path == "file.txt"
        assert len(patch.lines) == 7
        assert patch.lines[0] == b"--- a/file.txt\n"
        assert patch.lines[1] == b"+++ b/file.txt\n"
        assert patch.lines[2] == b"@@ -1,3 +1,3 @@\n"

    def test_single_file_multiple_hunks(self):
        """Test parsing a diff with one file and multiple hunks."""
        patches = list(collect_unified_diff(_two_hunk_diff_lines()))

        assert len(patches) == 2
        assert patches[0].old_path == "file.txt"
        assert patches[1].old_path == "file.txt"
        assert b"@@ -1,3 +1,3 @@" in patches[0].lines[2]
        assert b"@@ -10,3 +10,3 @@" in patches[1].lines[2]

    def test_scoped_parser_uses_line_buffers(self):
        """Test scoped parsing returns buffer-backed hunk payloads."""
        with acquire_unified_diff(_two_hunk_diff_lines()) as patches:
            patch = next(patches)

            assert isinstance(patch, SingleHunkPatch)
            assert isinstance(patch.lines, LineBuffer)
            assert b"".join(patch.lines).startswith(b"--- a/file.txt\n")

        with pytest.raises(ValueError, match="buffer is closed"):
            list(patch.lines)

    def test_scoped_parser_releases_previous_hunk_on_advance(self):
        """Test advancing a scoped parser closes the prior hunk payload."""
        with acquire_unified_diff(_two_hunk_diff_lines()) as patches:
            first = next(patches)
            assert isinstance(first, SingleHunkPatch)
            assert b"old 1" in b"".join(first.lines)

            second = next(patches)
            assert isinstance(second, SingleHunkPatch)
            assert b"old 2" in b"".join(second.lines)

            with pytest.raises(ValueError, match="buffer is closed"):
                list(first.lines)

        with pytest.raises(ValueError, match="buffer is closed"):
            list(second.lines)

    def test_scoped_parser_closes_source_on_early_exit(self):
        """Test closing a scoped parser closes an unfinished source iterator."""
        source_closed = False

        def source():
            nonlocal source_closed
            try:
                yield from _two_hunk_diff_lines()
            finally:
                source_closed = True

        with acquire_unified_diff(source()) as patches:
            patch = next(patches)
            assert isinstance(patch, SingleHunkPatch)
            assert b"old 1" in b"".join(patch.lines)

        assert source_closed
        with pytest.raises(ValueError, match="buffer is closed"):
            list(patch.lines)

    def test_multiple_files(self):
        """Test parsing a diff with multiple files."""
        diff = """\
diff --git a/file1.txt b/file1.txt
--- a/file1.txt
+++ b/file1.txt
@@ -1 +1 @@
-old 1
+new 1
diff --git a/file2.txt b/file2.txt
--- a/file2.txt
+++ b/file2.txt
@@ -1 +1 @@
-old 2
+new 2
"""
        patches = list(collect_unified_diff(diff.encode('utf-8').splitlines(keepends=True)))

        assert len(patches) == 2
        assert patches[0].old_path == "file1.txt"
        assert patches[1].old_path == "file2.txt"

    @pytest.mark.parametrize(
        ("hunk_header", "body"),
        [
            (b"@@ -1 +1 @@\n", b"---old\n+++new\n"),
            (b"@@ -1,2 +1,2 @@\n", b"---old\n+++new\n trailing\n"),
            (b"@@ -1,2 +1,2 @@\n", b" leading\n---old\n+++new\n"),
            (
                b"@@ -1,3 +1,3 @@\n",
                b" leading\n---old\n+++new\n trailing\n",
            ),
        ],
    )
    def test_header_like_changed_content_uses_declared_counts(
        self,
        hunk_header,
        body,
    ):
        """Changed lines resembling file headers remain in the hunk body."""
        diff = (
            b"diff --git a/file.txt b/file.txt\n"
            b"--- a/file.txt\n"
            b"+++ b/file.txt\n"
            + hunk_header
            + body
            + b"diff --git a/next.txt b/next.txt\n"
            b"--- a/next.txt\n"
            b"+++ b/next.txt\n"
            b"@@ -1 +1 @@\n"
            b"-before\n"
            b"+after\n"
        )

        patches = collect_unified_diff(diff.splitlines(keepends=True))

        assert len(patches) == 2
        assert b"---old\n+++new\n" in b"".join(patches[0].lines)
        assert patches[1].path() == "next.txt"

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            (b"-old\n", "before the hunk body was complete"),
            (b"-old\n+new\n+extra\n", "exceeds declared counts"),
            (b"-old\ninvalid\n", "Invalid line prefix"),
        ],
    )
    def test_malformed_hunk_body_is_rejected(self, body, message):
        """Malformed bodies never produce a partial parsed patch."""
        diff = (
            b"diff --git a/file.txt b/file.txt\n"
            b"--- a/file.txt\n"
            b"+++ b/file.txt\n"
            b"@@ -1 +1 @@\n"
            + body
        )

        with pytest.raises(CommandError, match=message):
            collect_unified_diff(diff.splitlines(keepends=True))

    @pytest.mark.parametrize(
        "following_line",
        [b"@@ -1 +1 @@\n", b"diff --git a/next.txt b/next.txt\n", None],
    )
    def test_malformed_file_headers_are_rejected(self, following_line):
        """An old-file header must be immediately followed by a new-file header."""
        lines = [
            b"diff --git a/file.txt b/file.txt\n",
            b"--- a/file.txt\n",
        ]
        if following_line is not None:
            lines.append(following_line)

        with pytest.raises(CommandError, match=r"missing \+\+\+|expected \+\+\+"):
            collect_unified_diff(lines)

    def test_malformed_diff_git_header_is_rejected(self):
        """A recognized but unparseable file header must not drop the file."""
        lines = [
            b"diff --git a/file.txt\n",
            b"--- a/file.txt\n",
            b"+++ b/file.txt\n",
            b"@@ -1 +1 @@\n",
            b"-old\n",
            b"+new\n",
        ]

        with pytest.raises(CommandError, match="Malformed diff --git header"):
            collect_unified_diff(lines)

    def test_new_file(self):
        """Test parsing a diff for a newly created file."""
        diff = """\
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..abc123
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+first line
+second line
"""
        patches = list(collect_unified_diff(diff.encode('utf-8').splitlines(keepends=True)))

        assert len(patches) == 1
        assert patches[0].old_path == "new.txt"
        assert patches[0].new_path == "new.txt"
        assert b"--- /dev/null" in patches[0].lines[0]

    def test_deleted_file(self):
        """Test parsing a diff for a deleted file."""
        diff = """\
diff --git a/deleted.txt b/deleted.txt
deleted file mode 100644
index abc123..0000000
--- a/deleted.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-first line
-second line
"""
        patches = list(collect_unified_diff(diff.encode('utf-8').splitlines(keepends=True)))

        assert len(patches) == 1
        assert patches[0].old_path == "deleted.txt"
        assert patches[0].new_path == "deleted.txt"
        assert b"+++ /dev/null" in patches[0].lines[1]

    def test_patch_path_queries(self):
        """Test detecting file path patch headers."""
        deleted_file_patch = [
            b"--- a/deleted.txt\n",
            b"+++ /dev/null\n",
            b"@@ -1 +0,0 @@\n",
        ]
        new_file_patch = [
            b"--- /dev/null\n",
            b"+++ b/new.txt\n",
            b"@@ -0,0 +1 @@\n",
        ]
        empty_file_patch = [
            b"--- /dev/null\n",
            b"+++ b/empty.txt\n",
            b"@@ -0,0 +0,0 @@\n",
        ]

        assert patch_is_file_deletion(deleted_file_patch)
        assert not patch_is_file_deletion(new_file_patch)
        assert patch_is_new_file(new_file_patch)
        assert not patch_is_new_file(deleted_file_patch)
        assert patch_is_empty_file_change(empty_file_patch)
        assert not patch_is_empty_file_change(new_file_patch)

    def test_empty_diff(self):
        """Test parsing an empty diff."""
        patches = list(collect_unified_diff([]))
        assert patches == []

    def test_no_newline_marker(self):
        """Test parsing diff with 'No newline at end of file' marker."""
        diff = """\
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old line
\\ No newline at end of file
+new line
"""
        patches = list(collect_unified_diff(diff.encode('utf-8').splitlines(keepends=True)))

        assert len(patches) == 1
        # Should include the backslash line
        assert any(b"\\" in line for line in patches[0].lines)

    def test_file_with_spaces_in_path(self):
        """Test parsing diff for files with spaces in the path."""
        diff = """\
diff --git a/path with spaces/file.txt b/path with spaces/file.txt
--- a/path with spaces/file.txt
+++ b/path with spaces/file.txt
@@ -1 +1 @@
-old
+new
"""
        patches = list(collect_unified_diff(diff.encode('utf-8').splitlines(keepends=True)))

        assert len(patches) == 1
        assert patches[0].old_path == "path with spaces/file.txt"

    def test_complex_multi_file_multi_hunk(self):
        """Test parsing a complex diff with multiple files and hunks."""
        diff = """\
diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,3 @@
 def foo():
-    return 1
+    return 2
     pass
@@ -10,2 +10,3 @@
 def bar():
+    x = 1
     pass
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1 +1,2 @@
 import sys
+import os
"""
        patches = list(collect_unified_diff(diff.encode('utf-8').splitlines(keepends=True)))

        assert len(patches) == 3
        assert patches[0].old_path == "file1.py"
        assert patches[1].old_path == "file1.py"
        assert patches[2].old_path == "file2.py"


class TestBuildLineLevelChangeFromPatchText:
    """Tests for build_line_changes_from_patch_lines function."""

    def _build_line_changes_from_patch_text(self, patch_text: str):
        return build_line_changes_from_patch_lines(
            patch_text.encode('utf-8').splitlines(keepends=True)
        )

    def test_build_line_changes_simple_addition(self):
        """Test building LineLevelChange from a simple addition patch."""
        patch_text = """--- a/test.txt
+++ b/test.txt
@@ -1,2 +1,3 @@
 line1
+added line
 line2
"""
        line_changes = self._build_line_changes_from_patch_text(patch_text)

        assert line_changes.path == "test.txt"
        assert line_changes.header.old_start == 1
        assert line_changes.header.old_len == 2
        assert line_changes.header.new_start == 1
        assert line_changes.header.new_len == 3
        assert len(line_changes.lines) == 3

        # Check line IDs are assigned to changed lines
        changed_ids = line_changes.changed_line_ids()
        assert changed_ids == [1]

    def test_build_line_changes_deletion(self):
        """Test building LineLevelChange from a deletion patch."""
        patch_text = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,2 @@
 keep1
-removed line
 keep2
"""
        line_changes = self._build_line_changes_from_patch_text(patch_text)

        assert line_changes.path == "file.py"
        changed_ids = line_changes.changed_line_ids()
        assert changed_ids == [1]

    def test_build_line_changes_multiple_changes(self):
        """Test building LineLevelChange from patch with multiple changes."""
        patch_text = """--- a/code.js
+++ b/code.js
@@ -1,4 +1,4 @@
 context1
-old line1
+new line1
 context2
-old line2
+new line2
"""
        line_changes = self._build_line_changes_from_patch_text(patch_text)

        assert line_changes.path == "code.js"
        changed_ids = line_changes.changed_line_ids()
        # Should have 4 changed lines (2 deletions + 2 additions)
        assert len(changed_ids) == 4

    def test_build_line_changes_from_line_iterable_matches_list(self):
        """Patch line iterables produce the same line changes as lists."""
        patch_bytes = b"""--- a/code.js
+++ b/code.js
@@ -1,3 +1,3 @@
 context
-old line
+new line
 tail
"""

        line_changes_from_list = build_line_changes_from_patch_lines(
            patch_bytes.splitlines(keepends=True)
        )
        line_changes_from_lines = build_line_changes_from_patch_lines(
            line for line in patch_bytes.splitlines(keepends=True)
        )

        assert line_changes_from_lines == line_changes_from_list
