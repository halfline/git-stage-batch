"""Tests for patch printing."""

from git_stage_batch.core.models import GitlinkChange
from git_stage_batch.output.patch import print_colored_patch, print_gitlink_change


def test_print_colored_patch_basic(capsys):
    """Test basic patch printing."""
    patch = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
 context
-old line
+new line
"""
    print_colored_patch(patch)
    captured = capsys.readouterr()
    assert "context" in captured.out
    assert "old line" in captured.out
    assert "new line" in captured.out


def test_print_colored_patch_empty(capsys):
    """Test printing empty patch."""
    print_colored_patch("")
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_gitlink_change_modified(capsys):
    """Test printing modified gitlink changes."""
    print_gitlink_change(
        GitlinkChange(
            old_path="sub",
            new_path="sub",
            old_oid="1111111111111111111111111111111111111111",
            new_oid="2222222222222222222222222222222222222222",
            change_type="modified",
        )
    )

    captured = capsys.readouterr()
    assert "sub :: Submodule pointer modified" in captured.out
    assert "old 111111111111" in captured.out
    assert "new 222222222222" in captured.out


def test_print_gitlink_change_added(capsys):
    """Test printing added gitlink changes."""
    print_gitlink_change(
        GitlinkChange(
            old_path="/dev/null",
            new_path="sub",
            old_oid=None,
            new_oid="2222222222222222222222222222222222222222",
            change_type="added",
        )
    )

    captured = capsys.readouterr()
    assert "sub :: Submodule added at 222222222222" in captured.out
