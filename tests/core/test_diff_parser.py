"""Tests for unified diff parser."""

import subprocess

import pytest

from git_stage_batch.core.diff_parser import (
    build_current_lines_from_patch_bytes,
    parse_unified_diff_into_single_hunk_patches,
    write_snapshots_for_current_file_path,
)
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import get_index_snapshot_file_path, get_working_tree_snapshot_file_path


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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

        assert len(patches) == 2
        assert patches[0].old_path == "file.txt"
        assert patches[1].old_path == "file.txt"
        assert b"@@ -1,3 +1,3 @@" in patches[0].lines[2]
        assert b"@@ -10,3 +10,3 @@" in patches[1].lines[2]

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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

        assert len(patches) == 1
        assert patches[0].old_path == "deleted.txt"
        assert patches[0].new_path == "deleted.txt"
        assert b"+++ /dev/null" in patches[0].lines[1]

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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

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
        patches = parse_unified_diff_into_single_hunk_patches(diff.encode('utf-8'))

        assert len(patches) == 3
        assert patches[0].old_path == "file1.py"
        assert patches[1].old_path == "file1.py"
        assert patches[2].old_path == "file2.py"


class TestGetFirstMatchingFileFromDiff:
    """Tests for get_first_matching_file_from_diff function."""

    @pytest.fixture
    def temp_git_repo(self, tmp_path, monkeypatch):
        """Create a temporary git repository for testing."""
        import subprocess
        repo = tmp_path / "test_repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True, cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

        # Create initial commit
        (repo / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=repo, capture_output=True)

        return repo

    def test_returns_first_file_when_no_predicate(self, temp_git_repo):
        """Test that without predicate, returns first file with changes."""
        from git_stage_batch.core.diff_parser import get_first_matching_file_from_diff
        import subprocess

        # Create and commit two files
        (temp_git_repo / "file1.txt").write_text("original 1\n")
        (temp_git_repo / "file2.txt").write_text("original 2\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        (temp_git_repo / "file1.txt").write_text("modified 1\n")
        (temp_git_repo / "file2.txt").write_text("modified 2\n")

        result = get_first_matching_file_from_diff(context_lines=3)

        # Should return first file (alphabetically by git)
        assert result in ["file1.txt", "file2.txt"]
        assert result is not None

    def test_returns_file_matching_predicate(self, temp_git_repo):
        """Test that returns first file where predicate matches."""
        from git_stage_batch.core.diff_parser import get_first_matching_file_from_diff

        # Create and commit two files
        (temp_git_repo / "file1.txt").write_text("original 1\n")
        (temp_git_repo / "file2.txt").write_text("original 2\n")
        import subprocess
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both
        (temp_git_repo / "file1.txt").write_text("modified 1\n")
        (temp_git_repo / "file2.txt").write_text("contains keyword\n")

        # Predicate that matches only file2
        def matches_keyword(patch_bytes: bytes) -> bool:
            return b"keyword" in patch_bytes

        result = get_first_matching_file_from_diff(context_lines=3, predicate=matches_keyword)

        assert result == "file2.txt"

    def test_returns_none_when_no_changes(self, temp_git_repo):
        """Test that returns None when there are no changes."""
        from git_stage_batch.core.diff_parser import get_first_matching_file_from_diff

        result = get_first_matching_file_from_diff(context_lines=3)

        assert result is None

    def test_returns_none_when_no_match(self, temp_git_repo):
        """Test that returns None when predicate never matches."""
        from git_stage_batch.core.diff_parser import get_first_matching_file_from_diff

        # Create a change
        (temp_git_repo / "file.txt").write_text("modified\n")

        # Predicate that never matches
        def never_matches(patch_text: str) -> bool:
            return False

        result = get_first_matching_file_from_diff(context_lines=3, predicate=never_matches)

        assert result is None


class TestBuildCurrentLinesFromPatchText:
    """Tests for build_current_lines_from_patch_text function."""

    def test_build_current_lines_simple_addition(self):
        """Test building CurrentLines from a simple addition patch."""
        patch_text = """--- a/test.txt
+++ b/test.txt
@@ -1,2 +1,3 @@
 line1
+added line
 line2
"""
        current_lines = build_current_lines_from_patch_bytes(patch_text.encode('utf-8'))

        assert current_lines.path == "test.txt"
        assert current_lines.header.old_start == 1
        assert current_lines.header.old_len == 2
        assert current_lines.header.new_start == 1
        assert current_lines.header.new_len == 3
        assert len(current_lines.lines) == 3

        # Check line IDs are assigned to changed lines
        changed_ids = current_lines.changed_line_ids()
        assert changed_ids == [1]

    def test_build_current_lines_deletion(self):
        """Test building CurrentLines from a deletion patch."""
        patch_text = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,2 @@
 keep1
-removed line
 keep2
"""
        current_lines = build_current_lines_from_patch_bytes(patch_text.encode('utf-8'))

        assert current_lines.path == "file.py"
        changed_ids = current_lines.changed_line_ids()
        assert changed_ids == [1]

    def test_build_current_lines_multiple_changes(self):
        """Test building CurrentLines from patch with multiple changes."""
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
        current_lines = build_current_lines_from_patch_bytes(patch_text.encode('utf-8'))

        assert current_lines.path == "code.js"
        changed_ids = current_lines.changed_line_ids()
        # Should have 4 changed lines (2 deletions + 2 additions)
        assert len(changed_ids) == 4


class TestWriteSnapshotsForCurrentFilePath:
    """Tests for write_snapshots_for_current_file_path with intent-to-add entries."""

    @pytest.fixture
    def temp_git_repo(self, tmp_path, monkeypatch):
        """Create a temporary git repository."""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

        # Create initial commit
        (tmp_path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

        # Ensure state directory exists
        from git_stage_batch.utils.paths import ensure_state_directory_exists
        ensure_state_directory_exists()

        return tmp_path

    def test_intent_to_add_tracked_file_uses_head_content(self, temp_git_repo):
        """When a tracked file has intent-to-add entry, index snapshot should use HEAD content."""
        # Create and commit a file
        test_file = temp_git_repo / "tracked.py"
        original_content = '''"""Module docstring."""

def original_function():
    """Original implementation."""
    return "original"
'''
        test_file.write_text(original_content)
        subprocess.run(["git", "add", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tracked file"], cwd=temp_git_repo, check=True, capture_output=True)

        # Modify the file in working tree
        modified_content = '''"""Module docstring."""

def original_function():
    """Modified implementation."""
    return "modified"

def new_function():
    """New function."""
    return "new"
'''
        test_file.write_text(modified_content)

        # Simulate intent-to-add by removing from cache and re-adding with -N
        # This creates an empty blob (e69de29...) in the index
        subprocess.run(["git", "rm", "--cached", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "-N", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)

        # Verify we have an empty blob in index
        ls_result = subprocess.run(
            ["git", "ls-files", "--stage", "tracked.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        assert "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391" in ls_result.stdout, "Should have empty blob in index"

        # Verify git show :file returns empty content (the bug scenario)
        show_result = subprocess.run(
            ["git", "show", ":tracked.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        assert show_result.stdout == "", "Index should return empty content for intent-to-add"

        # Call write_snapshots_for_current_file_path
        write_snapshots_for_current_file_path("tracked.py")

        # Read the index snapshot
        index_snapshot_path = get_index_snapshot_file_path()
        index_snapshot_content = read_text_file_contents(index_snapshot_path)

        # The fix: index snapshot should contain HEAD content, not empty
        assert index_snapshot_content == original_content, (
            "Index snapshot should contain HEAD content for intent-to-add tracked file, "
            "not empty content"
        )

        # Verify working tree snapshot has the modified content
        working_tree_snapshot_path = get_working_tree_snapshot_file_path()
        working_tree_snapshot_content = read_text_file_contents(working_tree_snapshot_path)
        assert working_tree_snapshot_content == modified_content

    def test_intent_to_add_new_file_keeps_empty_index(self, temp_git_repo):
        """New files with intent-to-add should keep empty index snapshot."""
        # Create a new file (not in HEAD)
        test_file = temp_git_repo / "newfile.py"
        new_content = '''"""New file."""

def new_function():
    return "new"
'''
        test_file.write_text(new_content)

        # Add with intent-to-add
        subprocess.run(["git", "add", "-N", "newfile.py"], cwd=temp_git_repo, check=True, capture_output=True)

        # Verify file is not in HEAD
        head_check = subprocess.run(
            ["git", "cat-file", "-e", "HEAD:newfile.py"],
            cwd=temp_git_repo,
            capture_output=True
        )
        assert head_check.returncode != 0, "New file should not exist in HEAD"

        # Call write_snapshots_for_current_file_path
        write_snapshots_for_current_file_path("newfile.py")

        # Read the index snapshot
        index_snapshot_path = get_index_snapshot_file_path()
        index_snapshot_content = read_text_file_contents(index_snapshot_path)

        # New files should have empty index snapshot (no fallback to HEAD)
        assert index_snapshot_content == "", "New file should have empty index snapshot"

        # Verify working tree snapshot has the content
        working_tree_snapshot_path = get_working_tree_snapshot_file_path()
        working_tree_snapshot_content = read_text_file_contents(working_tree_snapshot_path)
        assert working_tree_snapshot_content == new_content

    def test_normal_tracked_file_uses_index_content(self, temp_git_repo):
        """Normal tracked files should use index content (no fallback)."""
        # Create and commit a file
        test_file = temp_git_repo / "normal.py"
        original_content = "original content\n"
        test_file.write_text(original_content)
        subprocess.run(["git", "add", "normal.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add normal file"], cwd=temp_git_repo, check=True, capture_output=True)

        # Modify and stage
        staged_content = "staged content\n"
        test_file.write_text(staged_content)
        subprocess.run(["git", "add", "normal.py"], cwd=temp_git_repo, check=True, capture_output=True)

        # Further modify in working tree
        working_content = "working tree content\n"
        test_file.write_text(working_content)

        # Call write_snapshots_for_current_file_path
        write_snapshots_for_current_file_path("normal.py")

        # Index snapshot should have staged content
        index_snapshot_path = get_index_snapshot_file_path()
        index_snapshot_content = read_text_file_contents(index_snapshot_path)
        assert index_snapshot_content == staged_content

        # Working tree snapshot should have working content
        working_tree_snapshot_path = get_working_tree_snapshot_file_path()
        working_tree_snapshot_content = read_text_file_contents(working_tree_snapshot_path)
        assert working_tree_snapshot_content == working_content
