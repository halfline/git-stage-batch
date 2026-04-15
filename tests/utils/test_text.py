"""Tests for byte streaming utilities."""

import pytest

from git_stage_batch.utils.text import bytes_to_lines


class TestBytesToLines:
    """Tests for bytes_to_lines function."""

    def test_simple_lines(self):
        """Test basic line splitting on \\n."""
        chunks = [b"line1\nline2\nline3\n"]
        lines = list(bytes_to_lines(chunks))
        assert lines == [b"line1\n", b"line2\n", b"line3\n"]

    def test_incomplete_last_line(self):
        """Test line without trailing newline."""
        chunks = [b"line1\nline2"]
        lines = list(bytes_to_lines(chunks))
        assert lines == [b"line1\n", b"line2"]

    def test_chunked_input(self):
        """Test input split across multiple chunks."""
        chunks = [b"line", b"1\nlin", b"e2\n"]
        lines = list(bytes_to_lines(chunks))
        assert lines == [b"line1\n", b"line2\n"]

    def test_preserves_crlf(self):
        """Test that \\r\\n line endings are preserved."""
        chunks = [b"a\r\nb\r\nc"]
        lines = list(bytes_to_lines(chunks))
        assert lines == [b"a\r\n", b"b\r\n", b"c"]

    def test_preserves_cr_in_content(self):
        """Test that \\r characters in content are preserved."""
        chunks = [b"a\rb\nc"]
        lines = list(bytes_to_lines(chunks))
        # \r is part of content, not stripped
        assert lines == [b"a\rb\n", b"c"]

    def test_empty_chunks(self):
        """Test empty input."""
        chunks = []
        lines = list(bytes_to_lines(chunks))
        assert lines == []

    def test_single_empty_chunk(self):
        """Test single empty chunk."""
        chunks = [b""]
        lines = list(bytes_to_lines(chunks))
        assert lines == []

    def test_only_newlines(self):
        """Test input with only newlines."""
        chunks = [b"\n\n\n"]
        lines = list(bytes_to_lines(chunks))
        assert lines == [b"\n", b"\n", b"\n"]

    def test_mixed_line_endings(self):
        """Test that different line endings are preserved exactly."""
        chunks = [b"unix\nwindows\r\nmac\rno-newline"]
        lines = list(bytes_to_lines(chunks))
        # Only split on \n, preserve \r
        assert lines == [b"unix\n", b"windows\r\n", b"mac\rno-newline"]

    def test_binary_content(self):
        """Test that binary content (non-UTF-8) is preserved."""
        chunks = [b"text\nbin\x80\x05\xFF\nmore"]
        lines = list(bytes_to_lines(chunks))
        assert lines == [b"text\n", b"bin\x80\x05\xFF\n", b"more"]

    def test_generator_input(self):
        """Test that generator input works."""
        def gen():
            yield b"a\n"
            yield b"b\n"

        lines = list(bytes_to_lines(gen()))
        assert lines == [b"a\n", b"b\n"]

    def test_chunk_split_in_newline(self):
        """Test chunk boundary at newline character."""
        chunks = [b"line1", b"\n", b"line2\n"]
        lines = list(bytes_to_lines(chunks))
        assert lines == [b"line1\n", b"line2\n"]

    def test_large_line(self):
        """Test handling of large lines."""
        large_content = b"x" * 10000
        chunks = [large_content, b"\n", b"y\n"]
        lines = list(bytes_to_lines(chunks))
        assert len(lines) == 2
        assert lines[0] == large_content + b"\n"
        assert lines[1] == b"y\n"

    def test_type_error_on_non_bytes(self):
        """Test that non-bytes input raises TypeError."""
        with pytest.raises(TypeError, match="expected bytes-like object"):
            list(bytes_to_lines(["not bytes"]))
