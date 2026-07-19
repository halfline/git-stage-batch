import pytest

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.models import FileModeChange, RenameChange, SingleHunkPatch
from git_stage_batch.data import live_diff


def test_acquire_prepared_live_diff_keeps_hunks_until_context_exit(monkeypatch):
    diff_lines = b"""\
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
""".splitlines(keepends=True)
    monkeypatch.setattr(
        live_diff,
        "stream_live_git_diff",
        lambda **_kwargs: iter(diff_lines),
    )

    with live_diff.acquire_prepared_live_diff() as changes:
        change = changes[0]
        assert isinstance(change, SingleHunkPatch)
        assert isinstance(change.lines, LineBuffer)
        assert b"".join(change.lines).endswith(b"+new\n")

    with pytest.raises(ValueError, match="buffer is closed"):
        list(change.lines)


def test_group_live_diff_assigns_rename_partner_once():
    rename = RenameChange("old.txt", "new.txt")
    mode = FileModeChange("other.txt", "100644", "100755")

    grouped = live_diff.group_live_diff_by_file(
        ["old.txt", "new.txt", "other.txt"],
        [rename, mode],
    )

    assert grouped == {
        "old.txt": (),
        "new.txt": (rename,),
        "other.txt": (mode,),
    }


def test_paths_for_live_changes_includes_both_rename_partners():
    rename = RenameChange("old.txt", "new.txt")

    assert live_diff.paths_for_live_changes([rename]) == ("old.txt", "new.txt")
