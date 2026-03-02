"""Tests for line selection parsing and management."""

import pytest

from git_stage_batch.line_selection import (
    parse_line_id_specification,
    read_line_ids_file,
    write_line_ids_file,
)


class TestParseLineIdSpecification:
    """Tests for parse_line_id_specification."""

    def test_single_id(self):
        """Test parsing a single ID."""
        result = parse_line_id_specification("5")
        assert result == [5]

    def test_multiple_ids(self):
        """Test parsing comma-separated IDs."""
        result = parse_line_id_specification("1,3,5")
        assert result == [1, 3, 5]

    def test_simple_range(self):
        """Test parsing a simple range."""
        result = parse_line_id_specification("1-3")
        assert result == [1, 2, 3]

    def test_range_inclusive(self):
        """Test that ranges are inclusive on both ends."""
        result = parse_line_id_specification("5-7")
        assert result == [5, 6, 7]

    def test_mixed_ids_and_ranges(self):
        """Test parsing mixed IDs and ranges."""
        result = parse_line_id_specification("1,3,5-7,10")
        assert result == [1, 3, 5, 6, 7, 10]

    def test_reverse_range(self):
        """Test that reverse ranges are automatically corrected."""
        result = parse_line_id_specification("7-5")
        assert result == [5, 6, 7]

    def test_duplicate_ids_removed(self):
        """Test that duplicate IDs are removed."""
        result = parse_line_id_specification("1,2,2,3")
        assert result == [1, 2, 3]

    def test_overlapping_ranges(self):
        """Test that overlapping ranges are merged."""
        result = parse_line_id_specification("1-3,2-4")
        assert result == [1, 2, 3, 4]

    def test_whitespace_ignored(self):
        """Test that whitespace is ignored."""
        result = parse_line_id_specification(" 1 , 3 , 5 - 7 ")
        assert result == [1, 3, 5, 6, 7]

    def test_sorted_output(self):
        """Test that output is sorted."""
        result = parse_line_id_specification("10,1,5,3")
        assert result == [1, 3, 5, 10]

    def test_single_element_range(self):
        """Test range with same start and end."""
        result = parse_line_id_specification("5-5")
        assert result == [5]

    def test_large_range(self):
        """Test parsing a large range."""
        result = parse_line_id_specification("1-10")
        assert result == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    def test_multiple_ranges(self):
        """Test multiple ranges."""
        result = parse_line_id_specification("1-3,10-12,20-22")
        assert result == [1, 2, 3, 10, 11, 12, 20, 21, 22]

    def test_empty_string_error(self):
        """Test that empty string raises error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification("")

    def test_invalid_character_error(self):
        """Test that invalid characters raise error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification("1,a,3")

    def test_invalid_range_format_error(self):
        """Test that invalid range format raises error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification("1-2-3")

    def test_leading_comma_error(self):
        """Test that leading comma raises error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification(",1,2")

    def test_trailing_comma_error(self):
        """Test that trailing comma raises error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification("1,2,")

    def test_double_comma_error(self):
        """Test that double comma raises error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification("1,,2")

    def test_range_without_end_error(self):
        """Test that incomplete range raises error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification("1-")

    def test_range_without_start_error(self):
        """Test that incomplete range raises error."""
        with pytest.raises(SystemExit):
            parse_line_id_specification("-5")


class TestReadLineIdsFile:
    """Tests for read_line_ids_file."""

    def test_read_existing_file(self, tmp_path):
        """Test reading a file with line IDs."""
        file_path = tmp_path / "ids.txt"
        file_path.write_text("1\n3\n5\n")

        result = read_line_ids_file(file_path)
        assert result == [1, 3, 5]

    def test_read_nonexistent_file(self, tmp_path):
        """Test reading a nonexistent file returns empty list."""
        file_path = tmp_path / "nonexistent.txt"
        result = read_line_ids_file(file_path)
        assert result == []

    def test_read_empty_file(self, tmp_path):
        """Test reading an empty file."""
        file_path = tmp_path / "empty.txt"
        file_path.write_text("")

        result = read_line_ids_file(file_path)
        assert result == []

    def test_read_file_with_whitespace(self, tmp_path):
        """Test reading file with extra whitespace."""
        file_path = tmp_path / "ids.txt"
        file_path.write_text("  1  \n  3  \n  5  \n")

        result = read_line_ids_file(file_path)
        assert result == [1, 3, 5]

    def test_read_file_with_blank_lines(self, tmp_path):
        """Test reading file with blank lines."""
        file_path = tmp_path / "ids.txt"
        file_path.write_text("1\n\n3\n\n5\n")

        result = read_line_ids_file(file_path)
        assert result == [1, 3, 5]

    def test_read_file_with_non_numeric_lines(self, tmp_path):
        """Test that non-numeric lines are skipped."""
        file_path = tmp_path / "ids.txt"
        file_path.write_text("1\ninvalid\n3\n# comment\n5\n")

        result = read_line_ids_file(file_path)
        assert result == [1, 3, 5]

    def test_read_preserves_order(self, tmp_path):
        """Test that reading preserves the order from the file."""
        file_path = tmp_path / "ids.txt"
        file_path.write_text("5\n3\n1\n")

        result = read_line_ids_file(file_path)
        assert result == [5, 3, 1]  # Order preserved

    def test_read_allows_duplicates(self, tmp_path):
        """Test that reading preserves duplicates."""
        file_path = tmp_path / "ids.txt"
        file_path.write_text("1\n2\n2\n3\n")

        result = read_line_ids_file(file_path)
        assert result == [1, 2, 2, 3]  # Duplicates preserved


class TestWriteLineIdsFile:
    """Tests for write_line_ids_file."""

    def test_write_simple_list(self, tmp_path):
        """Test writing a simple list of IDs."""
        file_path = tmp_path / "ids.txt"
        write_line_ids_file(file_path, [1, 3, 5])

        content = file_path.read_text()
        assert content == "1\n3\n5\n"

    def test_write_creates_parent_directory(self, tmp_path):
        """Test that writing creates parent directories."""
        file_path = tmp_path / "nested" / "dir" / "ids.txt"
        write_line_ids_file(file_path, [1, 2, 3])

        assert file_path.exists()
        content = file_path.read_text()
        assert content == "1\n2\n3\n"

    def test_write_sorts_ids(self, tmp_path):
        """Test that IDs are sorted when written."""
        file_path = tmp_path / "ids.txt"
        write_line_ids_file(file_path, [5, 1, 3])

        content = file_path.read_text()
        assert content == "1\n3\n5\n"

    def test_write_removes_duplicates(self, tmp_path):
        """Test that duplicates are removed when writing."""
        file_path = tmp_path / "ids.txt"
        write_line_ids_file(file_path, [1, 2, 2, 3, 3, 3])

        content = file_path.read_text()
        assert content == "1\n2\n3\n"

    def test_write_empty_list(self, tmp_path):
        """Test writing an empty list."""
        file_path = tmp_path / "ids.txt"
        write_line_ids_file(file_path, [])

        content = file_path.read_text()
        assert content == ""

    def test_write_single_id(self, tmp_path):
        """Test writing a single ID."""
        file_path = tmp_path / "ids.txt"
        write_line_ids_file(file_path, [42])

        content = file_path.read_text()
        assert content == "42\n"

    def test_write_overwrites_existing(self, tmp_path):
        """Test that writing overwrites existing content."""
        file_path = tmp_path / "ids.txt"
        file_path.write_text("old content\n")

        write_line_ids_file(file_path, [1, 2, 3])

        content = file_path.read_text()
        assert content == "1\n2\n3\n"

    def test_write_accepts_set(self, tmp_path):
        """Test that write accepts a set as input."""
        file_path = tmp_path / "ids.txt"
        write_line_ids_file(file_path, {3, 1, 2})

        content = file_path.read_text()
        assert content == "1\n2\n3\n"

    def test_write_accepts_generator(self, tmp_path):
        """Test that write accepts a generator as input."""
        file_path = tmp_path / "ids.txt"
        write_line_ids_file(file_path, (i for i in [3, 1, 2]))

        content = file_path.read_text()
        assert content == "1\n2\n3\n"

    def test_roundtrip(self, tmp_path):
        """Test write and read roundtrip."""
        file_path = tmp_path / "ids.txt"
        original_ids = [1, 3, 5, 7, 9]

        write_line_ids_file(file_path, original_ids)
        read_ids = read_line_ids_file(file_path)

        assert read_ids == original_ids
