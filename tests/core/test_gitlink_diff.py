"""Tests for gitlink diff helper functions."""

from __future__ import annotations

from git_stage_batch.core import gitlink_diff


def test_gitlink_metadata_detection_reads_mode_and_index():
    """Mode-160000 metadata should identify gitlink diffs."""
    assert gitlink_diff.metadata_indicates_gitlink([b"new file mode 160000"])
    assert gitlink_diff.metadata_indicates_gitlink(
        [b"index 1111111..2222222 160000"]
    )
    assert not gitlink_diff.metadata_indicates_gitlink(
        [b"index 1111111..2222222 100644"]
    )
    assert gitlink_diff.gitlink_oids_from_index(
        [b"index 1111111..2222222 160000"]
    ) == ("1111111", "2222222")


def test_gitlink_paths_and_change_type_use_null_oid_sides():
    """Null object ids should produce added or deleted gitlink sides."""
    null_oid = "0" * 40

    assert gitlink_diff.non_null_git_oid(null_oid) is None
    assert gitlink_diff.gitlink_old_path("sub", null_oid) == "/dev/null"
    assert gitlink_diff.gitlink_new_path("sub", null_oid) == "/dev/null"
    assert gitlink_diff.gitlink_change_type([], null_oid, "1234567") == "added"
    assert gitlink_diff.gitlink_change_type([], "1234567", null_oid) == "deleted"
    assert gitlink_diff.gitlink_change_type([], "1234567", "89abcde") == "modified"


def test_gitlink_subproject_commit_patch_oids():
    """Subproject commit patch lines should yield old and new oids."""
    assert gitlink_diff.gitlink_oids_from_subproject_commit_patch(
        [
            b"--- a/sub\n",
            b"+++ b/sub\n",
            b"@@ -1 +1 @@\n",
            b"-Subproject commit 1111111-dirty\n",
            b"+Subproject commit 2222222\n",
        ]
    ) == ("1111111", "2222222")
    assert gitlink_diff.gitlink_oids_from_subproject_commit_patch(
        [
            b"--- a/sub\n",
            b"+++ b/sub\n",
            b"@@ -1 +1 @@\n",
        ]
    ) is None
    assert gitlink_diff.gitlink_oids_from_subproject_commit_patch(
        [
            b"-not a subproject line\n",
        ]
    ) is None


def test_consume_gitlink_hunks_stops_before_next_file():
    """Gitlink hunk consumption should leave the next diff header unread."""
    lines = iter(
        [
            b"-Subproject commit 1111111\n",
            b"+Subproject commit 2222222\n",
            b"diff --git a/next b/next\n",
        ]
    )
    lookahead = None

    def next_line():
        nonlocal lookahead
        if lookahead is not None:
            line = lookahead
            lookahead = None
            return line
        return next(lines, None)

    def peek_line():
        nonlocal lookahead
        if lookahead is None:
            lookahead = next(lines, None)
        return lookahead

    assert gitlink_diff.consume_gitlink_hunks(next_line, peek_line) == (
        "1111111",
        "2222222",
    )
    assert peek_line() == b"diff --git a/next b/next\n"
