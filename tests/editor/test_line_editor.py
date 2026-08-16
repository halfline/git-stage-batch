"""Tests for append-only indexed line storage."""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from git_stage_batch.core.buffer import LineBuffer
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


def test_appending_ranges_acquires_owner_leases_incrementally():
    """Append-only lease tracking should not rescan accumulated piece runs."""

    class CountingEditor(LineEditor):
        hash_count = 0

        def __hash__(self):
            self.hash_count += 1
            return id(self)

    source_editor = CountingEditor(())
    target_editor = LineEditor(())
    source_editor.append_line_range((b"one\n",), 0, 1)
    source_editor.hash_count = 0

    append_count = 500
    for _index in range(append_count):
        target_editor.append_line_ranges_from_editor(source_editor, 0, 1)

    assert source_editor.hash_count < append_count * 10
    target_editor.close()
    source_editor.close()


def test_append_cancellation_rolls_back_piece_and_new_owner_lease(monkeypatch):
    """A reported append failure must leave neither content nor a lease."""
    source_editor = LineEditor((b"one\n",))
    target_editor = LineEditor(())
    original_append = target_editor._pieces.append_line_range

    def append_then_cancel(*args, **kwargs):
        original_append(*args, **kwargs)
        raise KeyboardInterrupt("piece append cancelled")

    monkeypatch.setattr(target_editor._pieces, "append_line_range", append_then_cancel)

    with pytest.raises(KeyboardInterrupt, match="piece append cancelled"):
        target_editor.append_line_ranges_from_editor(source_editor, 0, 1)

    assert len(target_editor) == 0
    assert target_editor._incoming_editor_leases == {}
    assert source_editor._outgoing_editor_leases == set()
    source_editor.close()
    target_editor.close()


def test_owner_lease_registration_is_atomic_when_source_tracking_fails():
    """A failed source-side lease add must not leave a target-side lease."""

    class CancellingSet(set):
        def add(self, _value):
            raise KeyboardInterrupt("lease registration cancelled")

    source_editor = LineEditor((b"one\n",))
    target_editor = LineEditor(())
    source_editor._outgoing_editor_leases = CancellingSet()

    with pytest.raises(KeyboardInterrupt, match="lease registration cancelled"):
        target_editor.append_line_ranges_from_editor(source_editor, 0, 1)

    assert len(target_editor) == 0
    assert target_editor._incoming_editor_leases == {}
    source_editor.close()
    target_editor.close()


def test_appending_fragmented_editor_avoids_temporary_run_tuple() -> None:
    """Borrowing many pieces must not duplicate them as Python objects."""
    run_count = 8192
    with (
        LineBuffer.from_line_chunks(
            f"line {line_index}\n".encode()
            for line_index in range(run_count * 2)
        ) as line_buffer,
        line_buffer.acquire_lines() as lines,
    ):
        source_editor = LineEditor(())
        target_editor = LineEditor(())
        try:
            for line_index in range(0, len(lines), 2):
                source_editor.append_line_range(lines, line_index, line_index + 1)

            gc.collect()
            tracemalloc.start()
            try:
                target_editor.append_line_ranges_from_editor(
                    source_editor,
                    0,
                    len(source_editor),
                )
                current_heap, peak_heap = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            assert peak_heap - current_heap < 256 * 1024
            assert len(target_editor) == run_count
        finally:
            target_editor.close()
            source_editor.close()


def test_fragmented_editor_append_rolls_back_every_run_after_cancellation(
    monkeypatch,
) -> None:
    """Streaming a fragmented source is one atomic append operation."""
    source_editor = LineEditor(())
    target_editor = LineEditor((b"existing\n",))
    try:
        for content in (b"one\n", b"two\n", b"three\n"):
            source_editor.append_line_range((content,), 0, 1)

        original_append = target_editor._append_line_range
        append_count = 0

        def append_then_cancel(line_range):
            nonlocal append_count
            append_count += 1
            original_append(line_range)
            if append_count == 2:
                raise KeyboardInterrupt("fragmented append cancelled")

        monkeypatch.setattr(target_editor, "_append_line_range", append_then_cancel)

        with pytest.raises(KeyboardInterrupt, match="fragmented append cancelled"):
            target_editor.append_line_ranges_from_editor(
                source_editor,
                0,
                len(source_editor),
            )

        assert list(target_editor.line_chunks()) == [b"existing\n"]
        assert target_editor._incoming_editor_leases == {}
        assert source_editor._outgoing_editor_leases == set()
    finally:
        target_editor.close()
        source_editor.close()


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


def test_line_editor_retries_failed_owned_resource_close():
    """A retained resource that fails once must remain owned for a retry."""

    class RetryableResource:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1
            if self.close_count == 1:
                raise KeyboardInterrupt("close cancelled")

    resource = RetryableResource()
    editor = LineEditor(())
    editor.retain_resource(resource)

    with pytest.raises(KeyboardInterrupt, match="close cancelled"):
        editor.close()
    with pytest.raises(ValueError, match="editor is closed"):
        _ = len(editor)

    editor.close()
    assert resource.close_count == 2


def test_line_editor_retries_failed_resource_from_drained_lease_chain():
    """A borrower retry must retain a failed deferred source cleanup."""

    class RetryableResource:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1
            if self.close_count == 1:
                raise KeyboardInterrupt("source cleanup cancelled")

    resource = RetryableResource()
    source = LineEditor((b"line\n",))
    source.retain_resource(resource)
    borrower = LineEditor(())
    borrower.append_line_ranges_from_editor(source, 0, 1)
    with pytest.raises(ValueError, match="active leases"):
        source.close()

    with pytest.raises(KeyboardInterrupt, match="cleanup cancelled"):
        borrower.close()
    borrower.close()

    assert resource.close_count == 2


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
