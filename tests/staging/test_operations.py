"""Tests for line-level staging operations."""

import subprocess

import pytest

from git_stage_batch.core.models import LineLevelChange, HunkHeader, LineEntry
from git_stage_batch.staging.operations import (
    build_target_index_content_bytes_with_selected_lines,
    build_target_index_content_bytes_with_replaced_lines,
    build_target_index_content_with_selected_lines,
    build_target_working_tree_content_bytes_with_replaced_lines,
    build_target_working_tree_content_with_discarded_lines,
    update_index_with_blob_content,
)
from git_stage_batch.utils.paths import ensure_state_directory_exists


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    return repo


class TestBuildTargetIndexContent:
    """Tests for build_target_index_content_with_selected_lines."""

    def test_include_single_addition(self):
        """Test including a single added line."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "+", None, 2, text_bytes=b"new line", text="new line"),
            LineEntry(None, " ", 2, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, {1}, base_text)

        assert result == "line1\nnew line\nline2\n"

    def test_include_single_deletion(self):
        """Test including a single deleted line."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"deleted line", text="deleted line"),
            LineEntry(None, " ", 3, 2, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\ndeleted line\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, {1}, base_text)

        assert result == "line1\nline2\n"

    def test_skip_addition(self):
        """Test skipping an added line (not including it)."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "+", None, 2, text_bytes=b"new line", text="new line"),
            LineEntry(None, " ", 2, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, set(), base_text)

        # Not including the addition means base stays the same
        assert result == "line1\nline2\n"

    def test_skip_deletion(self):
        """Test skipping a deleted line (keeping it)."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"kept line", text="kept line"),
            LineEntry(None, " ", 3, 2, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\nkept line\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, set(), base_text)

        # Not including the deletion means line stays
        assert result == "line1\nkept line\nline2\n"

    def test_include_replacement(self):
        """Test including both deletion and addition (replacement)."""
        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"old line", text="old line"),
            LineEntry(2, "+", None, 2, text_bytes=b"new line", text="new line"),
            LineEntry(None, " ", 3, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\nold line\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, {1, 2}, base_text)

        assert result == "line1\nnew line\nline2\n"

    def test_partial_selection(self):
        """Test selecting only some changes from a hunk."""
        header = HunkHeader(1, 1, 1, 4)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"add1", text="add1"),
            LineEntry(2, "+", None, 2, text_bytes=b"add2", text="add2"),
            LineEntry(None, " ", 1, 3, text_bytes=b"context", text="context"),
            LineEntry(3, "+", None, 4, text_bytes=b"add3", text="add3"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "context\n"

        # Include only IDs 1 and 3, skip 2
        result = build_target_index_content_with_selected_lines(line_changes, {1, 3}, base_text)

        assert result == "add1\ncontext\nadd3\n"

    def test_multiple_deletions(self):
        """Test including multiple deletions."""
        header = HunkHeader(1, 4, 1, 1)
        lines = [
            LineEntry(1, "-", 1, None, text_bytes=b"delete1", text="delete1"),
            LineEntry(2, "-", 2, None, text_bytes=b"delete2", text="delete2"),
            LineEntry(3, "-", 3, None, text_bytes=b"delete3", text="delete3"),
            LineEntry(None, " ", 4, 1, text_bytes=b"kept", text="kept"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "delete1\ndelete2\ndelete3\nkept\n"

        result = build_target_index_content_with_selected_lines(line_changes, {1, 2, 3}, base_text)

        assert result == "kept\n"

    def test_hunk_at_beginning_of_file(self):
        """Test hunk starting at line 1."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"new first line", text="new first line"),
            LineEntry(None, " ", 1, 2, text_bytes=b"line1", text="line1"),
            LineEntry(None, " ", 2, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, {1}, base_text)

        assert result == "new first line\nline1\nline2\n"

    def test_hunk_at_end_of_file(self):
        """Test hunk at the end of a file."""
        header = HunkHeader(2, 1, 2, 2)
        lines = [
            LineEntry(None, " ", 2, 2, text_bytes=b"line2", text="line2"),
            LineEntry(1, "+", None, 3, text_bytes=b"new last line", text="new last line"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, {1}, base_text)

        assert result == "line1\nline2\nnew last line\n"

    def test_empty_base(self):
        """Test with empty base (new file)."""
        header = HunkHeader(0, 0, 1, 2)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"line1", text="line1"),
            LineEntry(2, "+", None, 2, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = ""

        result = build_target_index_content_with_selected_lines(line_changes, {1, 2}, base_text)

        assert result == "line1\nline2\n"

    def test_preserves_trailing_newline(self):
        """Test that trailing newline is preserved from base."""
        header = HunkHeader(1, 1, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "+", None, 2, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\n"

        result = build_target_index_content_with_selected_lines(line_changes, {1}, base_text)

        assert result.endswith("\n")

    def test_empty_include_set(self):
        """Test with empty include set (no changes applied)."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "+", None, 2, text_bytes=b"added", text="added"),
            LineEntry(None, " ", 2, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(line_changes, set(), base_text)

        # No changes should be applied
        assert result == "line1\nline2\n"

    def test_include_combined_file_hunk_preserves_gaps_between_real_hunks(self):
        """Combined file-scoped views should keep untouched lines between hunks."""
        header = HunkHeader(1, 15, 1, 13)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"from old import a", text="from old import a"),
            LineEntry(2, "+", None, 2, text_bytes=b"from new import a", text="from new import a"),
            LineEntry(None, " ", 3, 3, text_bytes=b"line3", text="line3"),
            LineEntry(None, " ", 4, 4, text_bytes=b"line4", text="line4"),
            LineEntry(None, " ", 5, 5, text_bytes=b"line5", text="line5"),
            LineEntry(None, " ", 11, 11, text_bytes=b"line11", text="line11"),
            LineEntry(None, " ", 12, 12, text_bytes=b"line12", text="line12"),
            LineEntry(3, "-", 13, None, text_bytes=b"ownership = old_value", text="ownership = old_value"),
            LineEntry(4, "-", 14, None, text_bytes=b"", text=""),
            LineEntry(5, "+", None, 13, text_bytes=b"selected_lines = selected_lines", text="selected_lines = selected_lines"),
            LineEntry(None, " ", 15, 14, text_bytes=b"if binary:", text="if binary:"),
        ]
        line_changes = LineLevelChange(path="test.py", header=header, lines=lines)
        base_content = (
            b"line1\n"
            b"from old import a\n"
            b"line3\n"
            b"line4\n"
            b"line5\n"
            b"line6\n"
            b"line7\n"
            b"line8\n"
            b"line9\n"
            b"line10\n"
            b"line11\n"
            b"line12\n"
            b"ownership = old_value\n"
            b"\n"
            b"if binary:\n"
        )

        result = build_target_index_content_bytes_with_selected_lines(
            line_changes,
            {1, 2, 3, 4, 5},
            base_content,
        )

        assert result == (
            b"line1\n"
            b"from new import a\n"
            b"line3\n"
            b"line4\n"
            b"line5\n"
            b"line6\n"
            b"line7\n"
            b"line8\n"
            b"line9\n"
            b"line10\n"
            b"line11\n"
            b"line12\n"
            b"selected_lines = selected_lines\n"
            b"if binary:\n"
        )

    def test_include_combined_file_hunk_keeps_insertions_with_their_anchor(self):
        """Combined file-scoped views should not move insertions to the file start."""
        header = HunkHeader(10, 3, 10, 4)
        lines = [
            LineEntry(1, "+", None, 10, text_bytes=b"inserted", text="inserted"),
            LineEntry(None, " ", 10, 11, text_bytes=b"line10", text="line10"),
            LineEntry(None, " ", 11, 12, text_bytes=b"line11", text="line11"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_content = (
            b"line1\n"
            b"line2\n"
            b"line3\n"
            b"line4\n"
            b"line5\n"
            b"line6\n"
            b"line7\n"
            b"line8\n"
            b"line9\n"
            b"line10\n"
            b"line11\n"
            b"line12\n"
        )

        result = build_target_index_content_bytes_with_selected_lines(
            line_changes,
            {1},
            base_content,
        )

        assert result == (
            b"line1\n"
            b"line2\n"
            b"line3\n"
            b"line4\n"
            b"line5\n"
            b"line6\n"
            b"line7\n"
            b"line8\n"
            b"line9\n"
            b"inserted\n"
            b"line10\n"
            b"line11\n"
            b"line12\n"
        )

    def test_include_combined_file_hunk_ignores_gap_markers_between_regions(self):
        """Synthetic gap markers should not consume real file content."""
        header = HunkHeader(1, 15, 1, 15)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"old-a", text="old-a"),
            LineEntry(2, "+", None, 2, text_bytes=b"new-a", text="new-a"),
            LineEntry(
                None,
                " ",
                None,
                None,
                text_bytes=b"... 7 more lines ...",
                text="... 7 more lines ...",
            ),
            LineEntry(None, " ", 10, 10, text_bytes=b"line10", text="line10"),
            LineEntry(3, "-", 11, None, text_bytes=b"old-b", text="old-b"),
            LineEntry(4, "+", None, 11, text_bytes=b"new-b", text="new-b"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_content = (
            b"line1\n"
            b"old-a\n"
            b"line3\n"
            b"line4\n"
            b"line5\n"
            b"line6\n"
            b"line7\n"
            b"line8\n"
            b"line9\n"
            b"line10\n"
            b"old-b\n"
        )

        result = build_target_index_content_bytes_with_selected_lines(
            line_changes,
            {1, 2, 3, 4},
            base_content,
        )

        assert result == (
            b"line1\n"
            b"new-a\n"
            b"line3\n"
            b"line4\n"
            b"line5\n"
            b"line6\n"
            b"line7\n"
            b"line8\n"
            b"line9\n"
            b"line10\n"
            b"new-b\n"
        )

    def test_replace_selection_does_not_consume_trailing_additions(self):
        """Replacement staging should not absorb adjacent pure insertions."""
        header = HunkHeader(1, 2, 1, 4)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"keep", text="keep"),
            LineEntry(1, "-", 2, None, text_bytes=b"old value", text="old value"),
            LineEntry(2, "+", None, 2, text_bytes=b"working value", text="working value"),
            LineEntry(3, "+", None, 3, text_bytes=b"extra line", text="extra line"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_content = b"keep\nold value\n"

        result = build_target_index_content_bytes_with_replaced_lines(
            line_changes,
            {1, 2},
            "staged value",
            base_content,
        )

        assert result == b"keep\nstaged value\n"

    def test_replace_selection_honors_old_line_numbers_after_gap_markers(self):
        """File-scoped replacement staging should stay anchored after omitted regions."""
        header = HunkHeader(2, 12, 2, 12)
        lines = [
            LineEntry(None, " ", 2, 2, text_bytes=b"line2", text="line2"),
            LineEntry(
                None,
                " ",
                None,
                None,
                text_bytes=b"... 7 more lines ...",
                text="... 7 more lines ...",
            ),
            LineEntry(1, "-", 10, None, text_bytes=b"line10", text="line10"),
            LineEntry(2, "+", None, 10, text_bytes=b"working10", text="working10"),
            LineEntry(None, " ", 11, 11, text_bytes=b"line11", text="line11"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_content = b"".join(f"line{i}\n".encode("utf-8") for i in range(1, 21))

        result = build_target_index_content_bytes_with_replaced_lines(
            line_changes,
            {1, 2},
            "staged10",
            base_content,
        )

        assert result == (
            b"line1\n"
            b"line2\n"
            b"line3\n"
            b"line4\n"
            b"line5\n"
            b"line6\n"
            b"line7\n"
            b"line8\n"
            b"line9\n"
            b"staged10\n"
            b"line11\n"
            b"line12\n"
            b"line13\n"
            b"line14\n"
            b"line15\n"
            b"line16\n"
            b"line17\n"
            b"line18\n"
            b"line19\n"
            b"line20\n"
        )

    def test_replace_selection_spans_multiple_file_scoped_regions(self):
        """File-scoped replacement staging should replace the full selected span."""
        header = HunkHeader(5, 32, 5, 38)
        lines = [
            LineEntry(None, " ", 5, 5, text_bytes=b"line5", text="line5"),
            LineEntry(1, "+", None, 6, text_bytes=b"change-one-a", text="change-one-a"),
            LineEntry(2, "+", None, 7, text_bytes=b"change-one-b", text="change-one-b"),
            LineEntry(None, " ", 6, 8, text_bytes=b"line6", text="line6"),
            LineEntry(
                None,
                " ",
                None,
                None,
                text_bytes=b"... 14 more lines ...",
                text="... 14 more lines ...",
            ),
            LineEntry(None, " ", 20, 22, text_bytes=b"line20", text="line20"),
            LineEntry(3, "+", None, 23, text_bytes=b"change-two-a", text="change-two-a"),
            LineEntry(4, "+", None, 24, text_bytes=b"change-two-b", text="change-two-b"),
            LineEntry(None, " ", 21, 25, text_bytes=b"line21", text="line21"),
            LineEntry(
                None,
                " ",
                None,
                None,
                text_bytes=b"... 14 more lines ...",
                text="... 14 more lines ...",
            ),
            LineEntry(None, " ", 35, 39, text_bytes=b"line35", text="line35"),
            LineEntry(5, "+", None, 40, text_bytes=b"change-three-a", text="change-three-a"),
            LineEntry(6, "+", None, 41, text_bytes=b"change-three-b", text="change-three-b"),
            LineEntry(None, " ", 36, 42, text_bytes=b"line36", text="line36"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_content = b"".join(f"line{i}\n".encode("utf-8") for i in range(1, 41))
        replacement_text = "".join(
            ["stage-one-a\n", "stage-one-b\n"]
            + [f"line{i}\n" for i in range(6, 21)]
            + ["stage-two-a\n", "stage-two-b\n"]
            + [f"line{i}\n" for i in range(21, 36)]
            + ["stage-three-a\n", "stage-three-b\n"]
        )

        result = build_target_index_content_bytes_with_replaced_lines(
            line_changes,
            {1, 2, 3, 4, 5, 6},
            replacement_text,
            base_content,
        )

        assert result == (
            b"".join(f"line{i}\n".encode("utf-8") for i in range(1, 6))
            + replacement_text.encode("utf-8")
            + b"".join(f"line{i}\n".encode("utf-8") for i in range(36, 41))
        )

    def test_replace_selection_trims_matching_edge_anchors(self):
        """Replacement staging should ignore unchanged edge anchors by default."""
        header = HunkHeader(1, 4, 1, 4)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"keep1", text="keep1"),
            LineEntry(1, "-", 2, None, text_bytes=b"old", text="old"),
            LineEntry(2, "+", None, 2, text_bytes=b"working", text="working"),
            LineEntry(None, " ", 3, 3, text_bytes=b"keep3", text="keep3"),
            LineEntry(None, " ", 4, 4, text_bytes=b"keep4", text="keep4"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_content = b"keep1\nold\nkeep3\nkeep4\n"

        result = build_target_index_content_bytes_with_replaced_lines(
            line_changes,
            {1, 2},
            "keep1\nstaged\nkeep3\nkeep4\n",
            base_content,
        )

        assert result == b"keep1\nstaged\nkeep3\nkeep4\n"

    def test_replace_selection_keeps_matching_edge_anchors_with_no_edge_overlap(self):
        """Replacement staging should preserve duplicated anchors when requested."""
        header = HunkHeader(1, 4, 1, 4)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"keep1", text="keep1"),
            LineEntry(1, "-", 2, None, text_bytes=b"old", text="old"),
            LineEntry(2, "+", None, 2, text_bytes=b"working", text="working"),
            LineEntry(None, " ", 3, 3, text_bytes=b"keep3", text="keep3"),
            LineEntry(None, " ", 4, 4, text_bytes=b"keep4", text="keep4"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        base_content = b"keep1\nold\nkeep3\nkeep4\n"

        result = build_target_index_content_bytes_with_replaced_lines(
            line_changes,
            {1, 2},
            "keep1\nstaged\nkeep3\nkeep4\n",
            base_content,
            trim_unchanged_edge_anchors=False,
        )

        assert result == b"keep1\nkeep1\nstaged\nkeep3\nkeep4\nkeep3\nkeep4\n"

    def test_working_tree_replace_selection_trims_matching_edge_anchors(self):
        """Working-tree replacement should ignore unchanged edge anchors by default."""
        header = HunkHeader(1, 4, 1, 4)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"keep1", text="keep1"),
            LineEntry(1, "-", 2, None, text_bytes=b"old", text="old"),
            LineEntry(2, "+", None, 2, text_bytes=b"working", text="working"),
            LineEntry(None, " ", 3, 3, text_bytes=b"keep3", text="keep3"),
            LineEntry(None, " ", 4, 4, text_bytes=b"keep4", text="keep4"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_content = b"keep1\nworking\nkeep3\nkeep4\n"

        result = build_target_working_tree_content_bytes_with_replaced_lines(
            line_changes,
            {1, 2},
            "keep1\nstaged\nkeep3\nkeep4\n",
            working_content,
        )

        assert result == b"keep1\nstaged\nkeep3\nkeep4\n"

    def test_working_tree_replace_selection_keeps_matching_edge_anchors_with_no_edge_overlap(self):
        """Working-tree replacement should preserve duplicated anchors when requested."""
        header = HunkHeader(1, 4, 1, 4)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"keep1", text="keep1"),
            LineEntry(1, "-", 2, None, text_bytes=b"old", text="old"),
            LineEntry(2, "+", None, 2, text_bytes=b"working", text="working"),
            LineEntry(None, " ", 3, 3, text_bytes=b"keep3", text="keep3"),
            LineEntry(None, " ", 4, 4, text_bytes=b"keep4", text="keep4"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_content = b"keep1\nworking\nkeep3\nkeep4\n"

        result = build_target_working_tree_content_bytes_with_replaced_lines(
            line_changes,
            {1, 2},
            "keep1\nstaged\nkeep3\nkeep4\n",
            working_content,
            trim_unchanged_edge_anchors=False,
        )

        assert result == b"keep1\nkeep1\nstaged\nkeep3\nkeep4\nkeep3\nkeep4\n"


class TestBuildTargetWorkingTreeContent:
    """Tests for build_target_working_tree_content_with_discarded_lines."""

    def test_discard_single_addition(self):
        """Test discarding a single added line."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "+", None, 2, text_bytes=b"added line", text="added line"),
            LineEntry(None, " ", 2, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "line1\nadded line\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(line_changes, {1}, working_text)

        # Discarding the addition removes it
        assert result == "line1\nline2\n"

    def test_discard_single_deletion(self):
        """Test discarding a deletion (reinserts the line)."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"deleted line", text="deleted line"),
            LineEntry(None, " ", 3, 2, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "line1\nline2\n"  # Line already deleted in working tree

        result = build_target_working_tree_content_with_discarded_lines(line_changes, {1}, working_text)

        # Discarding the deletion reinserts it
        assert result == "line1\ndeleted line\nline2\n"

    def test_keep_addition(self):
        """Test keeping an added line (not discarding)."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "+", None, 2, text_bytes=b"added line", text="added line"),
            LineEntry(None, " ", 2, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "line1\nadded line\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(line_changes, set(), working_text)

        # Not discarding means working tree stays the same
        assert result == "line1\nadded line\nline2\n"

    def test_keep_deletion(self):
        """Test keeping a deletion (not discarding)."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"deleted line", text="deleted line"),
            LineEntry(None, " ", 3, 2, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "line1\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(line_changes, set(), working_text)

        # Not discarding the deletion means it stays deleted
        assert result == "line1\nline2\n"

    def test_discard_replacement(self):
        """Test discarding a replacement (deletion + addition)."""
        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "-", 2, None, text_bytes=b"old line", text="old line"),
            LineEntry(2, "+", None, 2, text_bytes=b"new line", text="new line"),
            LineEntry(None, " ", 3, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "line1\nnew line\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(line_changes, {1, 2}, working_text)

        # Discarding both reverts to original
        assert result == "line1\nold line\nline2\n"

    def test_partial_discard(self):
        """Test discarding only some changes."""
        header = HunkHeader(1, 1, 1, 4)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"add1", text="add1"),
            LineEntry(2, "+", None, 2, text_bytes=b"add2", text="add2"),
            LineEntry(None, " ", 1, 3, text_bytes=b"context", text="context"),
            LineEntry(3, "+", None, 4, text_bytes=b"add3", text="add3"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "add1\nadd2\ncontext\nadd3\n"

        # Discard only ID 2
        result = build_target_working_tree_content_with_discarded_lines(line_changes, {2}, working_text)

        assert result == "add1\ncontext\nadd3\n"

    def test_multiple_additions(self):
        """Test discarding multiple additions."""
        header = HunkHeader(1, 1, 1, 4)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"add1", text="add1"),
            LineEntry(2, "+", None, 2, text_bytes=b"add2", text="add2"),
            LineEntry(3, "+", None, 3, text_bytes=b"add3", text="add3"),
            LineEntry(None, " ", 1, 4, text_bytes=b"kept", text="kept"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "add1\nadd2\nadd3\nkept\n"

        result = build_target_working_tree_content_with_discarded_lines(line_changes, {1, 2, 3}, working_text)

        assert result == "kept\n"

    def test_hunk_at_beginning(self):
        """Test discarding at beginning of file."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"added first", text="added first"),
            LineEntry(None, " ", 1, 2, text_bytes=b"line1", text="line1"),
            LineEntry(None, " ", 2, 3, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "added first\nline1\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(line_changes, {1}, working_text)

        assert result == "line1\nline2\n"

    def test_preserves_trailing_newline(self):
        """Test that trailing newline is preserved."""
        header = HunkHeader(1, 1, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line1", text="line1"),
            LineEntry(1, "+", None, 2, text_bytes=b"line2", text="line2"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)
        working_text = "line1\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(line_changes, {1}, working_text)

        assert result.endswith("\n")


class TestUpdateIndexWithBlobContent:
    """Tests for update_index_with_blob_content."""

    def test_update_new_file(self, temp_git_repo):
        """Test updating index with a new file."""
        ensure_state_directory_exists()

        # Create initial commit
        (temp_git_repo / "existing.txt").write_text("existing\n")
        subprocess.run(["git", "add", "existing.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Update index with new file
        update_index_with_blob_content("newfile.txt", b"new content\n")

        # Verify it's in the index
        result = subprocess.run(
            ["git", "ls-files", "--", "newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "newfile.txt" in result.stdout

        # Verify content
        result = subprocess.run(
            ["git", "show", ":newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == "new content\n"

    def test_update_existing_file(self, temp_git_repo):
        """Test updating an existing file in the index."""
        ensure_state_directory_exists()

        # Create initial commit
        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Update the index (not working tree)
        update_index_with_blob_content("file.txt", b"modified\n")

        # Verify index content changed
        result = subprocess.run(
            ["git", "show", ":file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == "modified\n"

        # Verify working tree is unchanged
        assert (temp_git_repo / "file.txt").read_text() == "original\n"

    def test_preserves_file_mode(self, temp_git_repo):
        """Test that file mode is preserved when updating."""
        ensure_state_directory_exists()

        # Create executable file
        (temp_git_repo / "script.sh").write_text("#!/bin/bash\necho hello\n")
        subprocess.run(["git", "add", "script.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "update-index", "--chmod=+x", "script.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Get original mode
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        original_mode = result.stdout.split()[0]

        # Update content
        update_index_with_blob_content("script.sh", b"#!/bin/bash\necho goodbye\n")

        # Verify mode is preserved
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        new_mode = result.stdout.split()[0]
        assert new_mode == original_mode

    def test_defaults_to_regular_file_mode(self, temp_git_repo):
        """Test that new files get regular file mode (100644)."""
        ensure_state_directory_exists()

        # Create initial commit
        (temp_git_repo / "dummy.txt").write_text("dummy\n")
        subprocess.run(["git", "add", "dummy.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add new file
        update_index_with_blob_content("newfile.txt", b"content\n")

        # Check mode
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        mode = result.stdout.split()[0]
        assert mode == "100644"
