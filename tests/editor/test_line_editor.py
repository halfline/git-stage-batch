"""Tests for append-only indexed line storage."""

from __future__ import annotations

import pytest

from git_stage_batch.editor.line_editor import LineEditor


def test_line_editor_appends_indexed_ranges(line_sequence):
    """Appended ranges remain indexable without copying their lines."""
    lines = line_sequence([
        b"skip\n",
        b"one\n",
        b"two\n",
        b"three\n",
        b"drop\n",
    ])

    with LineEditor(()) as editor:
        editor.append_line_range(lines, 1, 3)
        editor.append_line_range(lines, 3, 4)

        assert len(editor) == 3
        assert editor[0] == b"one\n"
        assert editor[-1] == b"three\n"
        assert list(editor[1:]) == [b"two\n", b"three\n"]
        assert b"".join(editor.line_chunks()) == b"one\ntwo\nthree\n"


def test_line_editor_appends_ranges_from_another_editor(line_sequence):
    """An editor can share a range already assembled by another editor."""
    lines = line_sequence([b"one\n", b"two\n", b"three\n"])

    with LineEditor(()) as source_editor:
        source_editor.append_line_range(lines, 0, 3)

        with LineEditor(()) as target_editor:
            target_editor.append_line_ranges_from_editor(source_editor, 1, 3)

            assert b"".join(target_editor.line_chunks()) == b"two\nthree\n"


def test_shared_ranges_keep_the_source_editor_open(line_sequence):
    """A source editor cannot close while another editor borrows its ranges."""
    lines = line_sequence([b"one\n"])
    source_editor = LineEditor(())
    target_editor = LineEditor(())
    try:
        source_editor.append_line_range(lines, 0, 1)
        target_editor.append_line_ranges_from_editor(source_editor, 0, 1)

        with pytest.raises(ValueError, match="active leases"):
            source_editor.close()

        target_editor.close()
        source_editor.close()
    finally:
        target_editor.close()
        source_editor.close()


def test_pending_close_releases_owned_resource_after_last_borrower(line_sequence):
    """Deferred editor close should retain and then release backing resources."""

    class Resource:
        close_count = 0

        def close(self):
            self.close_count += 1

    resource = Resource()
    source_editor = LineEditor(())
    target_editor = LineEditor(())
    source_editor.retain_resource(resource)
    source_editor.append_line_range(line_sequence([b"one\n"]), 0, 1)
    target_editor.append_line_ranges_from_editor(source_editor, 0, 1)

    with pytest.raises(ValueError, match="active leases"):
        source_editor.close()
    assert resource.close_count == 0
    assert target_editor[0] == b"one\n"

    target_editor.close()

    assert resource.close_count == 1
    with pytest.raises(ValueError, match="editor is closed"):
        _ = len(source_editor)


def test_explicit_empty_owner_retains_resource_for_appended_range():
    """An empty owner is still an owner rather than a false-like sentinel."""

    class Resource:
        closed = False

        def close(self):
            self.closed = True

    resource = Resource()
    source_editor = LineEditor(())
    target_editor = LineEditor(())
    source_editor.retain_resource(resource)
    target_editor.append_line_range(
        (b"one\n",),
        0,
        1,
        owner=source_editor,
    )

    with pytest.raises(ValueError, match="active leases"):
        source_editor.close()
    assert resource.closed is False

    target_editor.close()

    assert resource.closed is True


def test_pending_close_drains_long_lease_chain_without_recursion():
    """Claim-scale editor chains should close without using Python recursion."""
    root = LineEditor(())
    root.append_line_range((b"root\n",), 0, 1)
    current = root

    for index in range(1500):
        child = LineEditor(())
        child.append_line_ranges_from_editor(
            current,
            len(current) - 1,
            len(current),
        )
        child.append_line_range((f"{index}\n".encode(),), 0, 1)
        with pytest.raises(ValueError, match="active leases"):
            current.close()
        current = child

    current.close()

    with pytest.raises(ValueError, match="editor is closed"):
        _ = len(root)


def test_line_editor_rejects_invalid_append_ranges(line_sequence):
    """Append ranges must stay within their indexed source."""
    lines = line_sequence([b"one\n"])

    with LineEditor(()) as editor:
        with pytest.raises(ValueError, match="invalid line range"):
            editor.append_line_range(lines, 0, 2)


def test_line_editor_rejects_access_after_close(line_sequence):
    """Closing an editor makes its indexed contents unavailable."""
    editor = LineEditor(())
    editor.append_line_range(line_sequence([b"one\n"]), 0, 1)
    editor.close()

    with pytest.raises(ValueError, match="editor is closed"):
        _ = editor[0]
