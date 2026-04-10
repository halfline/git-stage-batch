"""Comprehensive edge case tests for parse_unified_diff_streaming.

Tests for handling:
- Empty files (new and deleted)
- Binary files
- Renamed files
- Files with no hunks
- Mixed scenarios
"""

from git_stage_batch.core.diff_parser import parse_unified_diff_streaming


class TestEmptyFileDeletion:
    """Test parsing diffs with deleted empty files."""

    def test_deleted_empty_file_followed_by_normal_file(self):
        """Deleted empty file should not prevent parsing subsequent files."""
        diff = b"""\
diff --git a/empty.txt b/empty.txt
deleted file mode 100644
index e69de29..0000000
--- a/empty.txt
+++ /dev/null
diff --git a/normal.txt b/normal.txt
index abc1234..def5678 100644
--- a/normal.txt
+++ b/normal.txt
@@ -1,3 +1,4 @@
 line1
 line2
+new line
 line3
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        # Should parse the normal.txt patch (empty.txt has no hunks)
        assert len(patches) == 1
        assert patches[0].new_path == "normal.txt"
        assert len(patches[0].lines) == 7  # ---, +++, @@, 4 body lines

    def test_deleted_empty_file_followed_by_new_file(self):
        """Deleted empty file followed by a new file."""
        diff = b"""\
diff --git a/deleted.txt b/deleted.txt
deleted file mode 100644
index e69de29..0000000
--- a/deleted.txt
+++ /dev/null
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..83db48f
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,3 @@
+line1
+line2
+line3
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        assert len(patches) == 1
        assert patches[0].new_path == "new.txt"
        assert patches[0].old_path == "new.txt"

    def test_multiple_deleted_empty_files(self):
        """Multiple deleted empty files in sequence."""
        diff = b"""\
diff --git a/empty1.txt b/empty1.txt
deleted file mode 100644
index e69de29..0000000
--- a/empty1.txt
+++ /dev/null
diff --git a/empty2.txt b/empty2.txt
deleted file mode 100644
index e69de29..0000000
--- a/empty2.txt
+++ /dev/null
diff --git a/normal.txt b/normal.txt
index abc1234..def5678 100644
--- a/normal.txt
+++ b/normal.txt
@@ -1,1 +1,2 @@
 line1
+line2
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        # Should only parse normal.txt (the empty files have no hunks)
        assert len(patches) == 1
        assert patches[0].new_path == "normal.txt"


class TestNewEmptyFiles:
    """Test parsing diffs with new empty files."""

    def test_new_empty_file_followed_by_normal_file(self):
        """New empty file should not block parsing of subsequent files."""
        diff = b"""\
diff --git a/empty.txt b/empty.txt
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/empty.txt
diff --git a/normal.txt b/normal.txt
index abc1234..def5678 100644
--- a/normal.txt
+++ b/normal.txt
@@ -1,1 +1,2 @@
 line1
+line2
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        assert len(patches) == 1
        assert patches[0].new_path == "normal.txt"


class TestBinaryFiles:
    """Test parsing diffs with binary files."""

    def test_binary_file_followed_by_text_file(self):
        """Binary file should not block parsing of subsequent text files."""
        diff = b"""\
diff --git a/image.png b/image.png
new file mode 100644
index 0000000..abcd123
Binary files /dev/null and b/image.png differ
diff --git a/text.txt b/text.txt
index abc1234..def5678 100644
--- a/text.txt
+++ b/text.txt
@@ -1,1 +1,2 @@
 line1
+line2
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        # Should parse text.txt (binary file has no traditional hunks)
        assert len(patches) == 1
        assert patches[0].new_path == "text.txt"

    def test_binary_change_between_text_files(self):
        """Binary file change in the middle of text file changes."""
        diff = b"""\
diff --git a/before.txt b/before.txt
index abc1234..def5678 100644
--- a/before.txt
+++ b/before.txt
@@ -1,1 +1,2 @@
 line1
+line2
diff --git a/image.png b/image.png
index old123..new456 100644
Binary files a/image.png and b/image.png differ
diff --git a/after.txt b/after.txt
index ghi789..jkl012 100644
--- a/after.txt
+++ b/after.txt
@@ -1,1 +1,2 @@
 lineA
+lineB
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        assert len(patches) == 2
        assert patches[0].new_path == "before.txt"
        assert patches[1].new_path == "after.txt"


class TestRenamedFiles:
    """Test parsing diffs with renamed files."""

    def test_renamed_file_without_changes(self):
        """Renamed file with no content changes."""
        diff = b"""\
diff --git a/old_name.txt b/new_name.txt
similarity index 100%
rename from old_name.txt
rename to new_name.txt
diff --git a/other.txt b/other.txt
index abc1234..def5678 100644
--- a/other.txt
+++ b/other.txt
@@ -1,1 +1,2 @@
 line1
+line2
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        # Should parse other.txt (renamed file has no hunks)
        assert len(patches) == 1
        assert patches[0].new_path == "other.txt"

    def test_renamed_file_with_changes(self):
        """Renamed file with content changes."""
        diff = b"""\
diff --git a/old_name.txt b/new_name.txt
similarity index 90%
rename from old_name.txt
rename to new_name.txt
index abc1234..def5678 100644
--- a/old_name.txt
+++ b/new_name.txt
@@ -1,3 +1,4 @@
 line1
 line2
+new line
 line3
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        assert len(patches) == 1
        assert patches[0].old_path == "old_name.txt"
        assert patches[0].new_path == "new_name.txt"


class TestModeChanges:
    """Test parsing diffs with mode changes."""

    def test_mode_change_only(self):
        """File with only mode change, no content changes."""
        diff = b"""\
diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
diff --git a/other.txt b/other.txt
index abc1234..def5678 100644
--- a/other.txt
+++ b/other.txt
@@ -1,1 +1,2 @@
 line1
+line2
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        # Should parse other.txt (mode change has no hunks)
        assert len(patches) == 1
        assert patches[0].new_path == "other.txt"

    def test_mode_change_with_content_changes(self):
        """File with both mode and content changes."""
        diff = b"""\
diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
index abc1234..def5678
--- a/script.sh
+++ b/script.sh
@@ -1,2 +1,3 @@
 #!/bin/bash
 echo "hello"
+echo "world"
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        assert len(patches) == 1
        assert patches[0].new_path == "script.sh"


class TestMixedScenarios:
    """Test complex scenarios with multiple edge cases."""

    def test_kitchen_sink(self):
        """Mix of empty deletions, binary files, renames, and normal changes."""
        diff = b"""\
diff --git a/deleted_empty.txt b/deleted_empty.txt
deleted file mode 100644
index e69de29..0000000
--- a/deleted_empty.txt
+++ /dev/null
diff --git a/new_empty.txt b/new_empty.txt
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/new_empty.txt
diff --git a/image.png b/image.png
new file mode 100644
index 0000000..abc123
Binary files /dev/null and b/image.png differ
diff --git a/old.txt b/new.txt
rename from old.txt
rename to new.txt
diff --git a/normal1.txt b/normal1.txt
index abc1234..def5678 100644
--- a/normal1.txt
+++ b/normal1.txt
@@ -1,1 +1,2 @@
 line1
+line2
diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
diff --git a/normal2.txt b/normal2.txt
index ghi789..jkl012 100644
--- a/normal2.txt
+++ b/normal2.txt
@@ -1,1 +1,2 @@
 lineA
+lineB
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        # Should parse only the files with actual hunks
        assert len(patches) == 2
        assert patches[0].new_path == "normal1.txt"
        assert patches[1].new_path == "normal2.txt"

    def test_intent_to_add_files(self):
        """Files added with git add -N (intent-to-add)."""
        diff = b"""\
diff --git a/file_a.py b/file_a.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/file_a.py
@@ -0,0 +1,3 @@
+# MARKER_A
+def func_a():
+    return 'A'
diff --git a/deleted_intent.py b/deleted_intent.py
deleted file mode 100644
index e69de29..0000000
--- a/deleted_intent.py
+++ /dev/null
diff --git a/file_b.py b/file_b.py
new file mode 100644
index 0000000..7654321
--- /dev/null
+++ b/file_b.py
@@ -0,0 +1,3 @@
+# MARKER_B
+def func_b():
+    return 'B'
"""
        patches = list(parse_unified_diff_streaming(diff.splitlines(keepends=True)))

        # Should parse both file_a.py and file_b.py
        # The deleted_intent.py has no hunks
        assert len(patches) == 2
        assert patches[0].new_path == "file_a.py"
        assert patches[1].new_path == "file_b.py"


class TestStreamingBehavior:
    """Test that streaming works correctly (early termination)."""

    def test_early_termination(self):
        """Should be able to stop iteration early."""
        diff = b"""\
diff --git a/file1.txt b/file1.txt
index abc1234..def5678 100644
--- a/file1.txt
+++ b/file1.txt
@@ -1,1 +1,2 @@
 line1
+line2
diff --git a/file2.txt b/file2.txt
index ghi789..jkl012 100644
--- a/file2.txt
+++ b/file2.txt
@@ -1,1 +1,2 @@
 lineA
+lineB
diff --git a/file3.txt b/file3.txt
index mno345..pqr678 100644
--- a/file3.txt
+++ b/file3.txt
@@ -1,1 +1,2 @@
 lineX
+lineY
"""
        # Take only first patch
        patches = []
        for patch in parse_unified_diff_streaming(diff.splitlines(keepends=True)):
            patches.append(patch)
            break

        assert len(patches) == 1
        assert patches[0].new_path == "file1.txt"
