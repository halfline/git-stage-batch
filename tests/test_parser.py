"""Tests for unified diff parser."""

import pytest

from git_stage_batch.parser import parse_unified_diff_into_single_hunk_patches


class TestParseUnifiedDiff:
    """Tests for parse_unified_diff_into_single_hunk_patches."""

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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

        assert len(patches) == 1
        patch = patches[0]
        assert patch.old_path == "file.txt"
        assert patch.new_path == "file.txt"
        assert len(patch.lines) == 7
        assert patch.lines[0] == "--- a/file.txt"
        assert patch.lines[1] == "+++ b/file.txt"
        assert patch.lines[2] == "@@ -1,3 +1,3 @@"

    def test_single_file_multiple_hunks(self):
        """Test parsing a diff with one file and multiple hunks."""
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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

        assert len(patches) == 2
        assert patches[0].old_path == "file.txt"
        assert patches[1].old_path == "file.txt"
        assert "@@ -1,3 +1,3 @@" in patches[0].lines[2]
        assert "@@ -10,3 +10,3 @@" in patches[1].lines[2]

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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

        assert len(patches) == 2
        assert patches[0].old_path == "file1.txt"
        assert patches[1].old_path == "file2.txt"

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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

        assert len(patches) == 1
        assert patches[0].old_path == "new.txt"
        assert patches[0].new_path == "new.txt"
        assert "--- /dev/null" in patches[0].lines[0]

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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

        assert len(patches) == 1
        assert patches[0].old_path == "deleted.txt"
        assert patches[0].new_path == "deleted.txt"
        assert "+++ /dev/null" in patches[0].lines[1]

    def test_empty_diff(self):
        """Test parsing an empty diff."""
        patches = parse_unified_diff_into_single_hunk_patches("")
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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

        assert len(patches) == 1
        # Should include the backslash line
        assert any("\\" in line for line in patches[0].lines)

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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

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
        patches = parse_unified_diff_into_single_hunk_patches(diff)

        assert len(patches) == 3
        assert patches[0].old_path == "file1.py"
        assert patches[1].old_path == "file1.py"
        assert patches[2].old_path == "file2.py"
