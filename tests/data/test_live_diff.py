import pytest

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.models import SingleHunkPatch
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
