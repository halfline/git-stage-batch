"""Heap and input-consumption bounds for streamed diff parsing."""

from git_stage_batch.core.diff_stream import DiffLineCursor


def test_cursor_peek_and_pushback_consume_each_input_line_once():
    consumed = []
    closed = []

    def lines():
        try:
            for line in (b"first\n", b"second\n", b"third\n"):
                consumed.append(line)
                yield line
        finally:
            closed.append(True)

    cursor = DiffLineCursor(lines())
    assert cursor.peek_line() == cursor.peek_line() == b"first\n"
    assert consumed == [b"first\n"]
    assert cursor.next_line() == b"first\n"
    second = cursor.next_line()
    cursor.push_back(second)
    assert cursor.peek_line() == cursor.next_line() == b"second\n"
    assert consumed == [b"first\n", b"second\n"]
    cursor.close()
    assert closed == [True]
