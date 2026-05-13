"""Tests for byte streaming utilities."""

import pytest

from git_stage_batch.editor import EditorBuffer
from git_stage_batch.utils.text import (
    bytes_to_lines,
    normalize_line_ending,
    normalize_line_endings,
    normalize_line_sequence_endings,
)


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


class TestNormalizeLineSequenceEndings:
    """Tests for lazy line-ending normalization over line sequences."""

    def test_normalizes_line_endings_on_access(self, line_sequence):
        """Line sequences are normalized without requiring a list input."""
        lines = line_sequence([b"one\r\n", b"two\r", b"three\n", b"four"])
        normalized = normalize_line_sequence_endings(lines)

        assert normalized[0] == b"one\n"
        assert normalized[1] == b"two\n"
        assert normalized[2] == b"three\n"
        assert normalized[3] == b"four"

    def test_normalizes_only_line_terminators(self, line_sequence):
        """Embedded carriage returns remain content in line sequences."""
        lines = line_sequence([b"one\rtwo\n", b"three\rfour\r\n"])
        normalized = normalize_line_sequence_endings(lines)

        assert normalized[0] == b"one\rtwo\n"
        assert normalized[1] == b"three\rfour\n"

    def test_supports_slices(self, line_sequence):
        """Normalized line sequences support slice access."""
        lines = line_sequence([b"one\r\n", b"two\r", b"three\n"])
        normalized = normalize_line_sequence_endings(lines)

        assert normalized[0:2] == [b"one\n", b"two\n"]

    def test_negative_indexes_follow_sequence_rules(self, line_sequence):
        """Normalized line sequences support negative indexes."""
        lines = line_sequence([b"one\r\n", b"two\r"])
        normalized = normalize_line_sequence_endings(lines)

        assert normalized[-1] == b"two\n"
        with pytest.raises(IndexError):
            normalized[-3]

    def test_repeated_access_does_not_cache_normalized_lines(self, line_sequence):
        """Repeated CRLF access returns equivalent but independent bytes."""
        lines = line_sequence([b"one\r\n"])
        normalized = normalize_line_sequence_endings(lines)

        assert normalized[0] == b"one\n"
        assert normalized[0] == b"one\n"
        assert normalized[0] is not normalized[0]

    def test_acquired_lf_lines_stay_scoped_views(self):
        """Normalized acquisitions preserve unchanged acquired lines."""
        with EditorBuffer.from_bytes(b"one\ntwo\r\nthree\r") as buffer:
            normalized = normalize_line_sequence_endings(buffer)
            with normalized.acquire_lines() as acquired:
                unchanged = acquired[0]
                crlf_line = acquired[1]
                cr_line = acquired[2]

                assert unchanged == b"one\n"
                assert not isinstance(unchanged, bytes)
                assert bytes(unchanged) == b"one\n"
                assert crlf_line == b"two\n"
                assert isinstance(crlf_line, bytes)
                assert cr_line == b"three\n"
                assert isinstance(cr_line, bytes)

        with pytest.raises(ValueError, match="line view is closed"):
            bytes(unchanged)

    def test_acquire_lines_accepts_plain_sequences(self, line_sequence):
        """Normalized acquisitions work without parent acquisition support."""
        lines = line_sequence([b"one\r\n", b"two\n"])
        normalized = normalize_line_sequence_endings(lines)

        with normalized.acquire_lines() as acquired:
            assert acquired[0] == b"one\n"
            assert acquired[1] == b"two\n"


class TestNormalizeLineEnding:
    """Tests for single-line terminator normalization."""

    def test_returns_lf_lines_unchanged(self):
        """LF-terminated and unterminated lines are already normalized."""
        line = b"one\n"
        unterminated = b"two"

        assert normalize_line_ending(line) is line
        assert normalize_line_ending(unterminated) is unterminated

    def test_rewrites_crlf_and_cr_terminators(self):
        """CRLF and CR terminators normalize to LF."""
        assert normalize_line_ending(b"one\r\n") == b"one\n"
        assert normalize_line_ending(b"two\r") == b"two\n"

    def test_preserves_embedded_carriage_returns(self):
        """Only the line terminator is normalized."""
        assert normalize_line_ending(b"one\rtwo\r\n") == b"one\rtwo\n"
        assert normalize_line_ending(b"one\rtwo\n") == b"one\rtwo\n"


class TestNormalizeLineEndings:
    """Tests for whole-buffer line-ending normalization."""

    def test_normalizes_embedded_carriage_returns(self):
        """Whole-buffer normalization still rewrites every CR."""
        assert normalize_line_endings(b"one\rtwo\r\n") == b"one\ntwo\n"
