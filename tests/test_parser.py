"""Tests for diff parsing functionality."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.models import HunkHeader, LineEntry
from git_stage_batch.parser import (
    DIFF_FILE_HEADER_PATTERN,
    HUNK_HEADER_PATTERN,
    build_current_lines_from_patch_text,
    get_path_from_patch_text,
    parse_unified_diff_into_single_hunk_patches,
    write_snapshots_for_current_file_path,
)
from git_stage_batch.state import (
    get_index_snapshot_file_path,
    get_working_tree_snapshot_file_path,
    read_text_file_contents,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "test.txt").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestPatterns:
    """Tests for regex patterns."""

    def test_diff_file_header_pattern(self):
        """Test DIFF_FILE_HEADER_PATTERN matches git diff headers."""
        match = DIFF_FILE_HEADER_PATTERN.match("diff --git a/file.txt b/file.txt")
        assert match is not None
        assert match.group(1) == "file.txt"
        assert match.group(2) == "file.txt"

    def test_diff_file_header_pattern_different_paths(self):
        """Test pattern matches renamed files."""
        match = DIFF_FILE_HEADER_PATTERN.match("diff --git a/old.txt b/new.txt")
        assert match is not None
        assert match.group(1) == "old.txt"
        assert match.group(2) == "new.txt"

    def test_diff_file_header_pattern_with_spaces(self):
        """Test pattern matches paths with spaces."""
        match = DIFF_FILE_HEADER_PATTERN.match("diff --git a/my file.txt b/my file.txt")
        assert match is not None
        assert match.group(1) == "my file.txt"
        assert match.group(2) == "my file.txt"

    def test_hunk_header_pattern(self):
        """Test HUNK_HEADER_PATTERN matches hunk headers."""
        match = HUNK_HEADER_PATTERN.match("@@ -10,5 +15,7 @@")
        assert match is not None
        assert match.group(1) == "10"  # old start
        assert match.group(2) == "5"   # old len
        assert match.group(3) == "15"  # new start
        assert match.group(4) == "7"   # new len

    def test_hunk_header_pattern_single_line(self):
        """Test pattern matches single-line hunks."""
        match = HUNK_HEADER_PATTERN.match("@@ -10 +15 @@")
        assert match is not None
        assert match.group(1) == "10"
        assert match.group(2) is None  # no length means 1
        assert match.group(3) == "15"
        assert match.group(4) is None


class TestParseUnifiedDiff:
    """Tests for parse_unified_diff_into_single_hunk_patches."""

    def test_single_file_single_hunk(self):
        """Test parsing a diff with one file and one hunk."""
        diff_text = """diff --git a/file.txt b/file.txt
index 1234567..abcdefg 100644
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 context line
-old line
+new line
 context line
"""
        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        assert len(patches) == 1
        assert patches[0].old_path == "file.txt"
        assert patches[0].new_path == "file.txt"
        assert len(patches[0].lines) == 7  # ---/+++/@@ + 4 body lines

    def test_single_file_multiple_hunks(self):
        """Test parsing a diff with one file and multiple hunks."""
        diff_text = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-old line 1
+new line 1
 context
@@ -10,2 +10,2 @@
 context
-old line 2
+new line 2
"""
        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        assert len(patches) == 2
        assert patches[0].old_path == "file.txt"
        assert patches[1].old_path == "file.txt"

    def test_multiple_files(self):
        """Test parsing a diff with multiple files."""
        diff_text = """diff --git a/file1.txt b/file1.txt
--- a/file1.txt
+++ b/file1.txt
@@ -1 +1 @@
-old
+new
diff --git a/file2.txt b/file2.txt
--- a/file2.txt
+++ b/file2.txt
@@ -1 +1 @@
-old2
+new2
"""
        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        assert len(patches) == 2
        assert patches[0].old_path == "file1.txt"
        assert patches[1].old_path == "file2.txt"

    def test_new_file(self):
        """Test parsing a diff for a new file."""
        diff_text = """diff --git a/newfile.txt b/newfile.txt
new file mode 100644
--- /dev/null
+++ b/newfile.txt
@@ -0,0 +1,2 @@
+new line 1
+new line 2
"""
        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        assert len(patches) == 1
        assert patches[0].old_path == "/dev/null"
        assert patches[0].new_path == "newfile.txt"

    def test_deleted_file(self):
        """Test parsing a diff for a deleted file."""
        diff_text = """diff --git a/deleted.txt b/deleted.txt
deleted file mode 100644
--- a/deleted.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-deleted line 1
-deleted line 2
"""
        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        assert len(patches) == 1
        assert patches[0].old_path == "deleted.txt"
        assert patches[0].new_path == "/dev/null"

    def test_empty_diff(self):
        """Test parsing an empty diff."""
        patches = parse_unified_diff_into_single_hunk_patches("")
        assert patches == []

    def test_no_newline_marker(self):
        """Test parsing diff with 'No newline at end of file' marker."""
        diff_text = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""
        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        assert len(patches) == 1


class TestBuildCurrentLines:
    """Tests for build_current_lines_from_patch_text."""

    def test_simple_patch(self):
        """Test building CurrentLines from a simple patch."""
        patch_text = """--- a/test.py
+++ b/test.py
@@ -5,4 +5,4 @@
 context1
-old line
+new line
 context2
"""
        current_lines = build_current_lines_from_patch_text(patch_text)

        assert current_lines.path == "test.py"
        assert current_lines.header.old_start == 5
        assert current_lines.header.old_len == 4
        assert current_lines.header.new_start == 5
        assert current_lines.header.new_len == 4
        assert len(current_lines.lines) == 4  # context1, -old, +new, context2

    def test_line_ids_assigned(self):
        """Test that changed lines get IDs assigned."""
        patch_text = """--- a/test.py
+++ b/test.py
@@ -1,5 +1,5 @@
 context
-deleted
+added
 context
"""
        current_lines = build_current_lines_from_patch_text(patch_text)

        # Check line IDs
        assert current_lines.lines[0].id is None  # context
        assert current_lines.lines[1].id == 1     # deleted
        assert current_lines.lines[2].id == 2     # added
        assert current_lines.lines[3].id is None  # context

    def test_line_kinds(self):
        """Test that line kinds are correctly assigned."""
        patch_text = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 context
-deleted
+added
"""
        current_lines = build_current_lines_from_patch_text(patch_text)

        assert current_lines.lines[0].kind == " "
        assert current_lines.lines[1].kind == "-"
        assert current_lines.lines[2].kind == "+"

    def test_line_numbers(self):
        """Test that line numbers are correctly tracked."""
        patch_text = """--- a/test.py
+++ b/test.py
@@ -10,3 +10,3 @@
 context
-deleted
+added
"""
        current_lines = build_current_lines_from_patch_text(patch_text)

        # Context line: both old and new
        assert current_lines.lines[0].old_line_number == 10
        assert current_lines.lines[0].new_line_number == 10

        # Deleted line: only old
        assert current_lines.lines[1].old_line_number == 11
        assert current_lines.lines[1].new_line_number is None

        # Added line: only new
        assert current_lines.lines[2].old_line_number is None
        assert current_lines.lines[2].new_line_number == 11

    def test_new_file_path(self):
        """Test path extraction for new files."""
        patch_text = """--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,2 @@
+line1
+line2
"""
        current_lines = build_current_lines_from_patch_text(patch_text)
        assert current_lines.path == "newfile.py"

    def test_deleted_file_path(self):
        """Test path extraction for deleted files."""
        patch_text = """--- a/oldfile.py
+++ /dev/null
@@ -1,2 +0,0 @@
-line1
-line2
"""
        current_lines = build_current_lines_from_patch_text(patch_text)
        assert current_lines.path == "oldfile.py"

    def test_missing_hunk_header_error(self):
        """Test that missing hunk header raises error."""
        patch_text = """--- a/test.py
+++ b/test.py
 context
-deleted
+added
"""
        with pytest.raises(SystemExit):
            build_current_lines_from_patch_text(patch_text)

    def test_bad_hunk_header_error(self):
        """Test that malformed hunk header raises error."""
        patch_text = """--- a/test.py
+++ b/test.py
@@ bad header @@
 context
"""
        with pytest.raises(SystemExit):
            build_current_lines_from_patch_text(patch_text)


class TestGetPathFromPatchText:
    """Tests for get_path_from_patch_text."""

    def test_get_path(self):
        """Test extracting path from patch."""
        patch_text = """--- a/test.py
+++ b/test.py
@@ -1 +1 @@
-old
+new
"""
        path = get_path_from_patch_text(patch_text)
        assert path == "test.py"


class TestWriteSnapshots:
    """Tests for write_snapshots_for_current_file_path."""

    def test_write_snapshots_file_in_index_and_worktree(self, temp_git_repo):
        """Test writing snapshots when file exists in both index and worktree."""
        from git_stage_batch.state import ensure_state_directory_exists

        ensure_state_directory_exists()

        # Modify the file
        (temp_git_repo / "test.txt").write_text("modified\n")

        write_snapshots_for_current_file_path("test.txt")

        index_snapshot = read_text_file_contents(get_index_snapshot_file_path())
        worktree_snapshot = read_text_file_contents(get_working_tree_snapshot_file_path())

        assert index_snapshot == "line1\nline2\nline3\n"  # original
        assert worktree_snapshot == "modified\n"  # modified

    def test_write_snapshots_new_file(self, temp_git_repo):
        """Test writing snapshots for a new file not in index."""
        from git_stage_batch.state import ensure_state_directory_exists

        ensure_state_directory_exists()

        # Create new file
        (temp_git_repo / "newfile.txt").write_text("new content\n")

        write_snapshots_for_current_file_path("newfile.txt")

        index_snapshot = read_text_file_contents(get_index_snapshot_file_path())
        worktree_snapshot = read_text_file_contents(get_working_tree_snapshot_file_path())

        assert index_snapshot == ""  # not in index
        assert worktree_snapshot == "new content\n"
