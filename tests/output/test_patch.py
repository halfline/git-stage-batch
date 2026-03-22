"""Tests for patch printing."""

from git_stage_batch.output.patch import print_colored_patch


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
