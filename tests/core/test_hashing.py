"""Tests for hunk hashing."""


from git_stage_batch.core.hashing import compute_stable_hunk_hash


class TestComputeStableHunkHash:
    """Tests for compute_stable_hunk_hash function."""

    def test_same_content_same_hash(self):
        """Test that identical content produces identical hashes."""
        patch = b"""\
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context
-old
+new
"""
        hash1 = compute_stable_hunk_hash(patch)
        hash2 = compute_stable_hunk_hash(patch)

        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """Test that different content produces different hashes."""
        patch1 = b"""\
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""
        patch2 = b"""\
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-different
+changed
"""
        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)

        assert hash1 != hash2

    def test_hash_is_hexadecimal_string(self):
        """Test that hash is a valid hexadecimal string."""
        patch = b"--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n"
        hash_value = compute_stable_hunk_hash(patch)

        # Should be a 40-character hex string (SHA1)
        assert len(hash_value) == 40
        assert all(c in "0123456789abcdef" for c in hash_value)

    def test_whitespace_sensitive(self):
        """Test that whitespace differences affect the hash."""
        patch1 = b"--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n"
        patch2 = b"--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old \n+new\n"

        hash1 = compute_stable_hunk_hash(patch1)
        hash2 = compute_stable_hunk_hash(patch2)

        assert hash1 != hash2

    def test_empty_string(self):
        """Test hashing an empty string."""
        hash_value = compute_stable_hunk_hash(b"")

        # Should still produce a valid hash
        assert len(hash_value) == 40

    def test_unicode_content(self):
        """Test hashing content with unicode characters."""
        patch = b"--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+\xe6\x96\xb0\xe3\x81\x97\xe3\x81\x84\n"  # "新しい" in UTF-8
        hash_value = compute_stable_hunk_hash(patch)

        # Should handle unicode without error
        assert len(hash_value) == 40
        assert all(c in "0123456789abcdef" for c in hash_value)
