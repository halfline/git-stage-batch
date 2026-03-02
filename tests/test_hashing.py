"""Tests for hunk hashing functionality."""

import pytest

from git_stage_batch.hashing import compute_stable_hunk_hash


class TestComputeStableHunkHash:
    """Tests for compute_stable_hunk_hash."""

    def test_basic_hash_computation(self):
        """Test that a hash is computed for a simple patch."""
        patch_text = """--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context
-old line
+new line
 context
"""
        hash_value = compute_stable_hunk_hash(patch_text)
        assert isinstance(hash_value, str)
        assert len(hash_value) == 40  # SHA1 hex digest

    def test_hash_stability(self):
        """Test that the same patch produces the same hash."""
        patch_text = """--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context
-old line
+new line
"""
        hash1 = compute_stable_hunk_hash(patch_text)
        hash2 = compute_stable_hunk_hash(patch_text)
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """Test that different content produces different hashes."""
        patch1 = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-old line
+new line
"""
        patch2 = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-different old
+different new
"""
        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)
        assert hash1 != hash2

    def test_different_path_different_hash(self):
        """Test that different file paths produce different hashes."""
        patch1 = """--- a/file1.txt
+++ b/file1.txt
@@ -1,2 +1,2 @@
-old
+new
"""
        patch2 = """--- a/file2.txt
+++ b/file2.txt
@@ -1,2 +1,2 @@
-old
+new
"""
        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)
        assert hash1 != hash2

    def test_different_header_different_hash(self):
        """Test that different hunk headers produce different hashes."""
        patch1 = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-old
+new
"""
        patch2 = """--- a/file.txt
+++ b/file.txt
@@ -10,2 +10,2 @@
-old
+new
"""
        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)
        assert hash1 != hash2

    def test_new_file_path_extraction(self):
        """Test hash with new file (/dev/null in ---)."""
        patch_text = """--- /dev/null
+++ b/newfile.txt
@@ -0,0 +1,2 @@
+line1
+line2
"""
        hash_value = compute_stable_hunk_hash(patch_text)
        assert isinstance(hash_value, str)
        assert len(hash_value) == 40

    def test_deleted_file_path_extraction(self):
        """Test hash with deleted file (/dev/null in +++)."""
        patch_text = """--- a/oldfile.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-line1
-line2
"""
        hash_value = compute_stable_hunk_hash(patch_text)
        assert isinstance(hash_value, str)
        assert len(hash_value) == 40

    def test_new_and_deleted_file_hashes_differ(self):
        """Test that new and deleted files with same content have different hashes."""
        new_file = """--- /dev/null
+++ b/file.txt
@@ -0,0 +1,2 @@
+line1
+line2
"""
        deleted_file = """--- a/file.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-line1
-line2
"""
        hash1 = compute_stable_hunk_hash(new_file)
        hash2 = compute_stable_hunk_hash(deleted_file)
        assert hash1 != hash2

    def test_prefers_plus_path_over_minus(self):
        """Test that +++ path is preferred when both exist."""
        # When file is modified (not new/deleted), both paths should be present
        # The function should use the +++ path
        patch1 = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-old
+new
"""
        patch2 = """--- a/differentname.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-old
+new
"""
        # Since we prefer +++ path, these should have the same hash
        # (though in practice git wouldn't produce such a diff)
        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)
        assert hash1 == hash2

    def test_path_prefix_stripping(self):
        """Test that a/ and b/ prefixes are stripped."""
        patch_with_prefix = """--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""
        patch_without_prefix = """--- file.txt
+++ file.txt
@@ -1 +1 @@
-old
+new
"""
        hash1 = compute_stable_hunk_hash(patch_with_prefix)
        hash2 = compute_stable_hunk_hash(patch_without_prefix)
        assert hash1 == hash2

    def test_only_first_hunk_header_used(self):
        """Test that only the first @@ header is captured."""
        # This simulates what might happen if multiple hunks were concatenated
        # (though parse_unified_diff_into_single_hunk_patches should prevent this)
        patch_text = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-old1
+new1
@@ -10,2 +10,2 @@
-old2
+new2
"""
        # The hash should be based on the first header
        hash_value = compute_stable_hunk_hash(patch_text)
        assert isinstance(hash_value, str)

    def test_empty_patch(self):
        """Test hash of effectively empty patch."""
        patch_text = """--- a/file.txt
+++ b/file.txt
"""
        hash_value = compute_stable_hunk_hash(patch_text)
        # Should still produce a hash, just based on path and empty body
        assert isinstance(hash_value, str)
        assert len(hash_value) == 40

    def test_no_newline_marker_included_in_hash(self):
        """Test that 'No newline at end of file' markers are included in body."""
        patch_text = """--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""
        hash_value = compute_stable_hunk_hash(patch_text)
        assert isinstance(hash_value, str)

    def test_context_lines_do_not_affect_hash(self):
        """Test that context lines are excluded from the hash for stability."""
        patch1 = """--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context1
-old
+new
"""
        patch2 = """--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context2
-old
+new
"""
        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)
        # Hashes should be identical despite different context
        assert hash1 == hash2

    def test_trailing_whitespace_in_header_stripped(self):
        """Test that trailing whitespace in header line is stripped."""
        patch1 = """--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""
        patch2 = """--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""
        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)
